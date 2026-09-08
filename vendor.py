#!/usr/bin/env python3
"""Vendor external Bazel dependencies into vendor/ and rewrite build files.

On HTTP 404 (or other irrecoverable fetch failures) the offending dependency is
gracefully patched out of the Bazel files instead of aborting the vendoring.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Repo root on which vendor.py operates.
ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
BCR_REGISTRY = VENDOR / "bcr-registry"
BCR_ARCHIVES = VENDOR / "bcr-archives"
MAVEN_REPO = VENDOR / "maven"

BCR_ORIGIN = "https://bcr.bazel.build"
MAVEN_CENTRAL = "https://repo1.maven.org/maven2"

SKIP_FILES = {
    # Bazel documentation/examples, not real downloads.
    "tools/build_defs/repo/http.bzl",
}

OverrideInfo = Dict[str, Any]


# ------------------------------- helpers -------------------------------

def sri_sha256(data: bytes) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode()


def hex_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_url(path: Path) -> str:
    """Return a file:// URL for an absolute path."""
    abspath = str(path.resolve())
    return "file://" + urllib.parse.quote(abspath, safe="/")


def fetch_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "vendor.py"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_bytes(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "vendor.py"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def try_download(url: str, dest: Path, expected_integrity: Optional[str] = None, dry_run: bool = False) -> Tuple[Optional[bytes], Optional[str]]:
    """Try to download *url* to *dest*.

    Returns (data, None) on success, (None, err_message) on failure.
    For HTTP 404 the error message starts with "404".
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        status = "[dry-run] would download"
        print(f"  {status} {url} -> {dest}")
        if dest.exists():
            return dest.read_bytes(), None
        return None, None

    if dest.exists():
        data = dest.read_bytes()
        if expected_integrity and sri_sha256(data) != expected_integrity:
            print(f"  checksum mismatch, re-downloading {url}")
        else:
            return data, None

    try:
        print(f"  downloading {url} -> {dest}")
        data = fetch_bytes(url)
        dest.write_bytes(data)
        if expected_integrity and sri_sha256(data) != expected_integrity:
            return None, f"integrity mismatch for {url}"
        return data, None
    except urllib.error.HTTPError as e:
        code = f"{e.code}"
        return None, f"{code} {e.reason}: {url}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}: {url}"


def download(url: str, dest: Path, expected_integrity: Optional[str] = None, dry_run: bool = False) -> Optional[bytes]:
    """Best-effort download used when callers want to proceed on success only."""
    data, err = try_download(url, dest, expected_integrity, dry_run)
    if err:
        # Do not emit here; let callers decide.
        return None
    return data


def iter_calls(text: str, names: tuple):
    """Yield (start, end, funcname, block) for balanced function calls."""
    pattern = re.compile(r"\b(" + "|".join(names) + r")\s*\(", re.DOTALL)
    i = 0
    while True:
        m = pattern.search(text, i)
        if not m:
            break
        start = m.start()
        depth = 1
        j = m.end()
        while j < len(text) and depth > 0:
            c = text[j]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            j += 1
        yield start, j, m.group(1), text[start:j]
        i = j


def backup(path: Path):
    back = path.with_suffix(path.suffix + ".vendor-bak")
    if not back.exists():
        shutil.copy2(path, back)


def rel_label(path: Path, anchor: Path = ROOT) -> str:
    """Return a Bazel label like //vendor/bcr/patches/foo/1.0:bar.patch"""
    rel = path.relative_to(anchor).as_posix()
    pkg, _, name = rel.rpartition("/")
    return ("//" + (pkg or rel)).rstrip(":/") + ":" + name


# ------------------------------- parsing -------------------------------

def parse_bazel_deps(module_text: str) -> Dict[str, str]:
    """Return {module_name: version} for bazel_dep entries in MODULE.bazel."""
    modules = {}
    for start, end, _, block in iter_calls(module_text, ("bazel_dep",)):
        name = re.search(r'\bname\s*=\s*"([^"]+)"', block)
        ver = re.search(r'\bversion\s*=\s*"([^"]+)"', block)
        if name and ver:
            modules[name.group(1)] = ver.group(1)
    return modules


def parse_module_overrides(module_text: str):
    """Return dict of local single_version_override info:
       {module: {"version": ..., "patch_strip": ..., "patches": [...]}}"""
    overrides = {}
    for _, _, _, block in iter_calls(module_text, ("single_version_override",)):
        mn = re.search(r'module_name\s*=\s*"([^"]+)"', block)
        ver = re.search(r'\bversion\s*=\s*"([^"]+)"', block)
        ps = re.search(r'patch_strip\s*=\s*(\d+)', block)
        patches_m = re.search(r'patches\s*=\s*\[(.*?)\]', block, re.DOTALL)
        if not mn:
            continue
        patches = []
        if patches_m:
            patches = re.findall(r'"([^"]+)"', patches_m.group(1))
        overrides[mn.group(1)] = {
            "version": ver.group(1) if ver else None,
            "patch_strip": int(ps.group(1)) if ps else 0,
            "patches": patches,
        }
    return overrides


def find_module_bazel_files() -> List[Path]:
    """Return every MODULE.bazel file in the workspace, except vendored ones."""
    files = []
    for path in ROOT.rglob("MODULE.bazel"):
        if VENDOR in path.parents:
            continue
        files.append(path)
    return sorted(files)


def resolve_patch_path(label_or_path: str, module_file: Path) -> Optional[Path]:
    """Resolve a patch reference from a MODULE.bazel to an absolute path.

    Labels like //pkg:file are resolved from the MODULE.bazel's own directory
    (the module's root). Plain relative paths are resolved from the containing
    MODULE.bazel.
    """
    label_or_path = label_or_path.strip()
    m = re.match(r"//([^:]*):(.+)", label_or_path)
    if m:
        pkg = m.group(1).replace(":", "/")
        return (module_file.parent / pkg / m.group(2)).resolve()
    return (module_file.parent / label_or_path).resolve()


def collect_workspace_modules(module_files: List[Path]) -> Tuple[Dict[str, List[str]], Dict[str, List[OverrideInfo]]]:
    """Collect all bazel_dep modules and single_version_override blocks
    declared across the workspace's MODULE.bazel files.

    Returns ({module_name: [versions]}, {module_name: [OverrideInfo]}).
    """
    all_modules: Dict[str, List[str]] = {}
    all_overrides: Dict[str, List[OverrideInfo]] = {}

    for path in module_files:
        text = path.read_text()
        for name, version in parse_bazel_deps(text).items():
            if version not in all_modules.setdefault(name, []):
                all_modules[name].append(version)
        for name, info in parse_module_overrides(text).items():
            resolved_patches = []
            for patch in info.get("patches", []):
                patch_path = resolve_patch_path(patch, path)
                if patch_path.exists():
                    resolved_patches.append(patch_path)
                else:
                    print(
                        f"  warning: patch {patch} for {name} not found relative to {path.relative_to(ROOT)}"
                    )
            all_overrides.setdefault(name, []).append({
                "version": info.get("version"),
                "patch_strip": info.get("patch_strip", 0),
                "patches": resolved_patches,
                "module_file": path,
            })

    for versions in all_modules.values():
        versions.sort()

    return all_modules, all_overrides


# ------------------------------- BCR local registry ---------------------

def vendor_bcr_registry(
    all_modules: Dict[str, List[str]],
    all_overrides: Dict[str, List[OverrideInfo]],
    dry_run: bool = False,
):
    """Create vendor/bcr-registry covering all bazel_dep modules.

    Returns (modules, missing_modules, overrides).
    """
    modules_to_process = []
    for name, versions in all_modules.items():
        for version in versions:
            modules_to_process.append((name, version))
    modules_to_process.sort()

    missing = set()
    registered_versions: Dict[str, List[str]] = {}

    BCR_REGISTRY.mkdir(parents=True, exist_ok=True)
    BCR_ARCHIVES.mkdir(parents=True, exist_ok=True)

    for name, version in modules_to_process:
        src_url = f"{BCR_ORIGIN}/modules/{name}/{version}/source.json"
        source, err = download_source_json(src_url, dry_run)
        if err:
            if err.startswith("404"):
                print(f"  404 for module {name}@{version}; will patch it out of MODULE.bazel")
                missing.add(name)
            else:
                print(f"  warning: could not fetch {src_url}: {err}")
            continue
        if not source:
            print(f"  warning: empty source.json for {src_url}")
            continue

        archive_url = source["url"]
        archive_basename = archive_url.split("/")[-1].split("?")[0] or f"{name}-{version}.tar.gz"
        archive_dest = BCR_ARCHIVES / f"{name}@{version}-{archive_basename}"
        archive_data, err = try_download(archive_url, archive_dest, source.get("integrity"), dry_run)
        if err:
            # If the archive is gone, treat the module as missing too.
            print(f"  {err} for module {name}@{version}; patching it out")
            missing.add(name)
            continue
        archive_integrity = sri_sha256(archive_data) if archive_data else source.get("integrity", "")

        # Build registry directories.
        mod_dir = BCR_REGISTRY / "modules" / name / version
        patches_dir = mod_dir / "patches"
        if not dry_run:
            mod_dir.mkdir(parents=True, exist_ok=True)
            patches_dir.mkdir(parents=True, exist_ok=True)

        # Download registry patch files and overlay files.
        patches_map = {}
        patch_strip = source.get("patch_strip", 0)
        for patch_name, integrity in source.get("patches", {}).items():
            patch_url = f"{BCR_ORIGIN}/modules/{name}/{version}/patches/{patch_name}"
            patch_dest = mod_dir / "patches" / patch_name
            patch_data, err = try_download(patch_url, patch_dest, integrity, dry_run)
            if err:
                if err.startswith("404"):
                    print(f"  warning: patch {patch_name} not found in BCR, skipping")
                else:
                    print(f"  warning: could not download patch {patch_name}: {err}")
                continue
            if patch_data:
                patches_map[patch_name] = sri_sha256(patch_data)

        overlay_map = {}
        for overlay_path, integrity in source.get("overlay", {}).items():
            overlay_url = f"{BCR_ORIGIN}/modules/{name}/{version}/{overlay_path}"
            overlay_dest = mod_dir / overlay_path
            if not dry_run:
                overlay_dest.parent.mkdir(parents=True, exist_ok=True)
            overlay_data, err = try_download(overlay_url, overlay_dest, integrity, dry_run)
            if err:
                if err.startswith("404"):
                    print(f"  warning: overlay {overlay_path} not found in BCR, skipping")
                else:
                    print(f"  warning: could not download overlay {overlay_path}: {err}")
                continue
            if overlay_data:
                overlay_map[overlay_path] = sri_sha256(overlay_data)

        # Fetch MODULE.bazel from registry (metadata only; already in archive).
        mod_bazel_url = f"{BCR_ORIGIN}/modules/{name}/{version}/MODULE.bazel"
        mod_bazel_dest = mod_dir / "MODULE.bazel"
        if not dry_run:
            try:
                mod_bazel_data = fetch_bytes(mod_bazel_url)
                mod_bazel_dest.write_bytes(mod_bazel_data)
            except Exception as e:
                print(f"  warning: could not fetch {mod_bazel_url}: {e}")

        # Merge local override patches.
        applicable_overrides = [
            o for o in all_overrides.get(name, [])
            if o.get("version") is None or o["version"] == version
        ]
        for override in applicable_overrides:
            for src_path in override["patches"]:
                patch_name = src_path.name
                patch_dest = patches_dir / patch_name
                data = src_path.read_bytes()
                if not dry_run:
                    shutil.copy2(src_path, patch_dest)
                patches_map[patch_name] = sri_sha256(data)
            patch_strip = max(patch_strip, override.get("patch_strip", patch_strip))

        new_source = {
            "url": file_url(archive_dest) if archive_data else archive_url,
            "integrity": archive_integrity,
        }
        if source.get("strip_prefix"):
            new_source["strip_prefix"] = source["strip_prefix"]
        if patches_map:
            new_source["patch_strip"] = patch_strip
            new_source["patches"] = patches_map
        if overlay_map:
            new_source["overlay"] = overlay_map

        if not dry_run:
            (mod_dir / "source.json").write_text(json.dumps(new_source, indent=2) + "\n")

        registered_versions.setdefault(name, []).append(version)

    if not dry_run:
        for name, versions in registered_versions.items():
            (BCR_REGISTRY / "modules" / name / "metadata.json").write_text(
                json.dumps({"versions": versions, "yanked_versions": {}}, indent=2) + "\n"
            )
        (BCR_REGISTRY / "bazel_registry.json").write_text(json.dumps({"mirrors": []}, indent=2) + "\n")

    return all_modules, missing, all_overrides


def download_source_json(url: str, dry_run: bool):
    """Return (source_json_dict, error_message)."""
    if dry_run:
        print(f"  [dry-run] would fetch {url}")
        return None, "dry-run"
    try:
        return fetch_json(url), None
    except urllib.error.HTTPError as e:
        return None, f"{e.code} {e.reason}: {url}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}: {url}"


# ------------------------------- HTTP rules -----------------------------

def vendor_build_files(dry_run: bool = False) -> Dict[Path, List[str]]:
    """Find http_file / http_archive / http_jar rules and rewrite their URLs.

    Returns a map of file paths to names of rules that were removed because
    their artifacts returned 404.
    """
    files_to_patch = []
    for ext in (".bzl", ".bazel", "BUILD", "BUILD.bazel"):
        for p in ROOT.rglob(ext):
            rel = p.relative_to(ROOT).as_posix()
            if rel in SKIP_FILES or "vendor" in p.parts:
                continue
            text = p.read_text()
            if re.search(r"\b(http_file|http_archive|http_jar)\s*\(", text):
                files_to_patch.append(p)

    # Add specific files that might not match extension list.
    for explicit in [Path("MODULE.bazel"), Path("tools/test/extensions.bzl"), Path("tools/android/android_extensions.bzl"), Path("repositories.bzl"), Path("derived/maven/defs.bzl")]:
        if (ROOT / explicit).exists():
            files_to_patch.append(ROOT / explicit)
    files_to_patch = sorted({p.resolve() for p in files_to_patch})

    stats = {"files": 0, "rules": 0}
    removed: Dict[Path, List[str]] = {}

    for path in files_to_patch:
        text = path.read_text()
        new_text = text
        replacements = []  # list of (start, end, new_block, rule_name, reason)
        for start, end, fname, block in iter_calls(text, ("http_file", "http_archive", "http_jar")):
            name_match = re.search(r'\bname\s*=\s*"([^"]+)"', block)
            rule_name = name_match.group(1) if name_match else "unknown"
            urls_match = re.search(r'\burls\s*=\s*\[(.*?)\]', block, re.DOTALL)
            url_single = re.search(r'\burl\s*=\s*"([^"]+)"', block)
            urls = []
            if urls_match:
                urls = re.findall(r'"([^"]+)"', urls_match.group(1))
            elif url_single:
                urls = [url_single.group(1)]
            if not urls:
                continue

            dest = vendor_dest_for_rule(rule_name, urls[0], fname)
            data, err = try_download(urls[0], dest, None, dry_run)
            if err:
                if err.startswith("404"):
                    print(f"  404 for @{rule_name}; removing its {fname} block from {path.relative_to(ROOT)}")
                    replacements.append((start, end, "", rule_name, "404"))
                else:
                    print(f"  warning: could not fetch {urls[0]}: {err}; leaving @{rule_name} unchanged")
                continue
            if data is None:
                continue
            file_url_str = file_url(dest)
            new_block = rewrite_http_block(block, file_url_str, dest.name, data)
            replacements.append((start, end, new_block, rule_name, None))

        if replacements:
            # Apply removals/replacements from end to start to keep indices valid.
            for start, end, new_block, rule_name, reason in sorted(replacements, reverse=True):
                if reason == "404":
                    removed.setdefault(path, []).append(rule_name)
                    # Clean up any immediately following whitespace/newlines to avoid
                    # leaving big gaps in the file.
                    while end < len(new_text) and new_text[end] == "\n":
                        end += 1
                    # And preceding whitespace that may have existed for indentation.
                    while start > 0 and new_text[start - 1] == "\n":
                        start -= 1
                new_text = new_text[:start] + new_block + new_text[end:]
            if not dry_run:
                backup(path)
                path.write_text(new_text)
                print(f"patched {path.relative_to(ROOT)}")
            else:
                removes = sum(1 for _, _, _, _, r in replacements if r == "404")
                rewrites = len(replacements) - removes
                print(f"  [dry-run] would patch {path.relative_to(ROOT)} ({rewrites} rewrites, {removes} removals)")
            stats["files"] += 1
            stats["rules"] += len(replacements)

    return removed


def vendor_dest_for_rule(name: str, url: str, func: str) -> Path:
    """Choose a deterministic vendor path for an HTTP-sourced file/archive."""
    parsed = urllib.parse.urlparse(url)
    basename = Path(parsed.path).name or "download"
    # Drop query/fragment for cleanliness.
    basename = basename.split("?")[0].split("#")[0]
    if not basename:
        basename = "download"
    # Categorise based on name heuristic.
    if name:
        if name.startswith("openjdk"):
            return VENDOR / "jdk" / basename
        if name.startswith("jq_"):
            return VENDOR / "bin" / "jq" / basename
        if name.startswith("async_profiler"):
            return VENDOR / "async_profiler" / basename
        if "coverage" in name.lower() or "remote_coverage" in name.lower():
            return VENDOR / "coverage" / basename
        if name.startswith("android_"):
            return VENDOR / "android" / basename
    # Otherwise use rule type dir.
    if func == "http_file":
        if basename.endswith(".jar"):
            return VENDOR / "maven" / maven_path_from_url(url, basename)
        return VENDOR / "bin" / (name or "unknown") / basename
    return VENDOR / "archives" / (name or "unknown") / basename


def maven_path_from_url(url: str, basename: str) -> Path:
    """If the URL is a maven artifact, return group/artifact/version/basename path."""
    for host in ("repo1.maven.org/maven2/", "maven.google.com/"):
        idx = url.find(host)
        if idx != -1:
            rest = url[idx + len(host):]
            # Strip leading version segment, e.g. 'v1/'
            rest = re.sub(r"^v\d+\/", "", rest)
            return Path(rest)
    return Path(basename)


def rewrite_http_block(block: str, file_url: str, filename: str, data: bytes) -> str:
    """Replace URLs and hashes inside a http_file/archive/jar block."""
    new_block = re.sub(
        r'\burls\s*=\s*\[.*?\]',
        f'urls = ["{file_url}"]',
        block,
        count=1,
        flags=re.DOTALL,
    )
    new_block = re.sub(
        r'\burl\s*=\s*"[^"]+"',
        f'url = "{file_url}"',
        new_block,
        count=1,
    )
    sha = hex_sha256(data)
    integrity = sri_sha256(data)
    new_block = re.sub(r'\bsha256\s*=\s*"[^"]*"', f'sha256 = "{sha}"', new_block, count=1)
    new_block = re.sub(r'\bintegrity\s*=\s*"[^"]*"', f'integrity = "{integrity}"', new_block, count=1)
    # Remove netrc if present (used by rules_jvm_external, not needed locally).
    new_block = re.sub(r'\s*netrc\s*=\s*"[^"]*",?', '', new_block)
    return new_block


# ------------------------------- .bazelrc -------------------------------

def update_bazelrc(dry_run: bool = False):
    path = ROOT / ".bazelrc"
    if not path.exists():
        print("  note: .bazelrc not found, skipping")
        return
    marker_start = "# --- vendor.py managed BCR registry start ---"
    marker_end = "# --- vendor.py managed BCR registry end ---"
    block = (
        f"{marker_start}\n"
        "common --registry=" + file_url(BCR_REGISTRY) + "\n"
        "# Fallback to the public Bazel Central Registry if a module is missing locally.\n"
        "common --registry=https://bcr.bazel.build\n"
        f"{marker_end}\n"
    )
    text = path.read_text()
    if marker_start in text and marker_end in text:
        pre = text[:text.index(marker_start)]
        post = text[text.index(marker_end) + len(marker_end):]
        text = pre + post
    text = block + text
    if not dry_run:
        backup(path)
        path.write_text(text)
        print("updated .bazelrc")
    else:
        print("  [dry-run] would update .bazelrc")


# ------------------------------- MODULE.bazel cleanup ---------------------

def remove_missing_bazel_deps(module_text: str, missing: set, module_dir: Path, dry_run: bool = False) -> str:
    """Remove bazel_dep entries (and any overrides) for modules that could not
    be fetched from the Bazel Central Registry."""
    new_text = module_text
    removals = []  # (start, end, kind, module_name)

    for start, end, _, block in iter_calls(module_text, ("bazel_dep",)):
        name = re.search(r'\bname\s*=\s*"([^"]+)"', block)
        if name and name.group(1) in missing:
            removals.append((start, end, "bazel_dep", name.group(1)))

    for start, end, _, block in iter_calls(module_text, ("single_version_override",)):
        mn = re.search(r'module_name\s*=\s*"([^"]+)"', block)
        if mn and mn.group(1) in missing:
            removals.append((start, end, "single_version_override", mn.group(1)))

    for start, end, _, block in iter_calls(module_text, ("local_path_override",)):
        mn = re.search(r'module_name\s*=\s*"([^"]+)"', block)
        if mn and mn.group(1) in missing:
            removals.append((start, end, "local_path_override", mn.group(1)))

    for start, end, _, _ in sorted(removals, reverse=True):
        # Nibble surrounding blank lines left by the removal.
        while start > 0 and new_text[start - 1] == "\n":
            start -= 1
        while end < len(new_text) and new_text[end] == "\n":
            end += 1
        new_text = new_text[:start] + new_text[end:]

    # Also remove any single_version_override blocks whose patches point to files
    # that no longer exist or were removed from the project.  This is mainly to
    # keep the build working after removing missing modules.
    if not dry_run:
        for start, end, _, block in sorted(iter_calls(new_text, ("single_version_override",)), reverse=True):
            patches_m = re.search(r'patches\s*=\s*\[(.*?)\]', block, re.DOTALL)
            if patches_m:
                all_ok = True
                mn_match = re.search(r'module_name\s*=\s*"([^"]+)"', block)
                for label in re.findall(r'"([^"]+)"', patches_m.group(1)):
                    m = re.match(r"//([^:]*):(.+)", label)
                    if not m:
                        continue
                    pkg = m.group(1).replace(":", "/")
                    src_path = (module_dir / pkg / m.group(2)).resolve()
                    if not src_path.exists():
                        all_ok = False
                        module_name = mn_match.group(1) if mn_match else "unknown"
                        print(f"  removing single_version_override for module {module_name} because patch {label} is missing")
                        break
                if not all_ok:
                    while start > 0 and new_text[start - 1] == "\n":
                        start -= 1
                    while end < len(new_text) and new_text[end] == "\n":
                        end += 1
                    new_text = new_text[:start] + new_text[end:]

    return new_text


def remove_maven_repositories_line(module_text: str) -> str:
    """Optionally rewrite maven repository URL to point to local maven repo."""
    return re.sub(
        r'(repositories\s*=\s*\[)[^\]]*(https://repo1\.maven\.org/maven2[^\]]*)',
        lambda m: m.group(1) + file_url(MAVEN_REPO) + "]",
        module_text,
    )


def update_module_bazel(path: Path, missing: set, dry_run: bool = False):
    if not path.exists():
        return

    module_text = path.read_text()

    # Step 1: remove missing modules (bazel_dep + overrides).
    new_text = remove_missing_bazel_deps(module_text, missing, path.parent, dry_run)

    # Step 2: remove all single_version_override blocks because their local
    # patches have been merged into vendor/bcr-registry source.json entries.
    blocks_to_remove = []
    for start, end, _, block in iter_calls(new_text, ("single_version_override",)):
        blocks_to_remove.append((start, end))
    for start, end in sorted(blocks_to_remove, reverse=True):
        while start > 0 and new_text[start - 1] == "\n":
            start -= 1
        while end < len(new_text) and new_text[end] == "\n":
            end += 1
        new_text = new_text[:start] + new_text[end:]

    # Step 3: point maven repositories to local mirror.
    new_text = remove_maven_repositories_line(new_text)

    if new_text != module_text:
        if not dry_run:
            backup(path)
            path.write_text(new_text)
            print(f"updated {path.relative_to(ROOT)}")
        else:
            print(f"  [dry-run] would update {path.relative_to(ROOT)}")
    return new_text


# ------------------------------- report -------------------------------

def write_removed_report(missing_bcr: set, removed_http: Dict[Path, List[str]]):
    """Record what was patched out so users can review and fix later."""
    report = VENDOR / "REMOVED.md"
    lines = ["# Dependencies patched out during vendoring\n\n"]
    if missing_bcr:
        lines.append("## BCR modules (source.json returned 404)\n\n")
        for m in sorted(missing_bcr):
            lines.append(f"- `{m}`\n")
        lines.append("\n")
    if removed_http:
        lines.append("## HTTP rules (artifact URL returned 404)\n\n")
        lines.append("| File | Rule |\n")
        lines.append("|------|------|\n")
        for path, names in sorted(removed_http.items(), key=lambda x: str(x[0])):
            for name in names:
                lines.append(f"| `{path.relative_to(ROOT).as_posix()}` | `{name}` |\n")
        lines.append("\n")
    if not missing_bcr and not removed_http:
        lines.append("No dependencies were removed.\n")
    report.write_text("".join(lines))
    print(f"\nWritten removal report to {report.relative_to(ROOT)}")


# ------------------------------- main -----------------------------------

def main():
    parser = argparse.ArgumentParser(description="Vendor Bazel external dependencies.")
    parser.add_argument("--dry-run", action="store_true", help="Plan only, do not download or modify files.")
    args = parser.parse_args()

    dry_run = args.dry_run
    if dry_run:
        print("DRY-RUN mode: no files will be changed and archives will not be downloaded.\n")

    module_files = find_module_bazel_files()
    print(f"Found {len(module_files)} MODULE.bazel file(s) in the workspace")

    all_modules, all_overrides = collect_workspace_modules(module_files)

    print("\n1. Building local BCR registry in vendor/bcr-registry ...")
    modules, missing_bcr, overrides = vendor_bcr_registry(all_modules, all_overrides, dry_run)
    total_versions = sum(len(v) for v in modules.values())
    print(f"   -> {total_versions - len(missing_bcr)} module version(s) registered ({len(missing_bcr)} missing)")

    print("\n2. Updating .bazelrc to use the local BCR registry (and public fallback) ...")
    update_bazelrc(dry_run)

    print("\n3. Vendoring explicit http_file/http_archive/http_jar rules ...")
    removed_http = vendor_build_files(dry_run)
    total_removed = sum(len(v) for v in removed_http.values())
    print(f"   -> patched files; {total_removed} artifact(s) removed due to 404")

    print("\n4. Patching out missing BCR modules from MODULE.bazel files ...")
    for path in module_files:
        update_module_bazel(path, missing_bcr, dry_run)

    if not dry_run:
        write_removed_report(missing_bcr, removed_http)

    print("\nDone.")
    if dry_run:
        print("Re-run without --dry-run to perform the vendoring.")


if __name__ == "__main__":
    main()
