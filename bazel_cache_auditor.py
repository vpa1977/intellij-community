#!/usr/bin/env python3
"""Audit a Bazel external cache directory for binary files.

Reports ELF binaries, JAR files, and archives that contain binary files
(other archives, ELF binaries, or JAR files).
"""

import argparse
import os
import sys
import tarfile
import zipfile

# Archive extensions inspected recursively.
ARCHIVE_EXTENSIONS = {
    ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tbz2",
    ".tar.xz", ".txz", ".tar.lz", ".zip", ".zipx",
    ".war", ".ear", ".nar",
}

ELF_MAGIC = b"\x7fELF"
ZIP_MAGIC = b"PK\x03\x04"
ZIP_MAGIC_EMPTY = b"PK\x05\x06"


def _read_magic(path: str, size: int = 4) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(size)
    except (OSError, IOError):
        return b""


def is_elf(path: str) -> bool:
    return _read_magic(path, 4) == ELF_MAGIC


def is_zip(path: str) -> bool:
    magic = _read_magic(path, 4)
    return magic == ZIP_MAGIC or magic == ZIP_MAGIC_EMPTY


def _has_archive_extension(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in ARCHIVE_EXTENSIONS)


def _is_jar(name: str) -> bool:
    return name.lower().endswith(".jar")


def is_archive(path: str) -> bool:
    name = os.path.basename(path)
    if _has_archive_extension(name):
        return True
    try:
        if tarfile.is_tarfile(path):
            return True
    except (OSError, IOError):
        pass
    return is_zip(path)


def _magic_is_binary(magic: bytes) -> bool:
    return magic == ELF_MAGIC or magic == ZIP_MAGIC or magic == ZIP_MAGIC_EMPTY


def _tar_contains_binary(path: str) -> bool:
    try:
        with tarfile.open(path, "r:*") as tf:
            for member in tf.getmembers():
                if member.isdir() or member.issym() or member.islnk():
                    continue
                name = member.name
                if _has_archive_extension(name) or _is_jar(name):
                    return True
                if not member.isfile():
                    continue
                f = tf.extractfile(member)
                if f is None:
                    continue
                try:
                    magic = f.read(4)
                finally:
                    f.close()
                if _magic_is_binary(magic):
                    return True
    except (tarfile.TarError, OSError, IOError):
        return False
    return False


def _zip_contains_binary(path: str) -> bool:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if _has_archive_extension(name) or _is_jar(name):
                    return True
                try:
                    with zf.open(info) as f:
                        magic = f.read(4)
                except (OSError, IOError, zipfile.BadZipFile, RuntimeError):
                    continue
                if _magic_is_binary(magic):
                    return True
    except (zipfile.BadZipFile, OSError, IOError):
        return False
    return False


def archive_contains_binary(path: str) -> bool:
    if tarfile.is_tarfile(path):
        return _tar_contains_binary(path)
    if zipfile.is_zipfile(path):
        return _zip_contains_binary(path)
    return False


def _iter_cache_files(cache_path: str):
    """Yield file paths under cache_path, following directory symlinks while avoiding loops."""
    visited_dirs = set()
    stack = [cache_path]
    while stack:
        current = stack.pop()
        if current == cache_path + "/_main":
            continue
        try:
            st = os.stat(current)
            key = (st.st_dev, st.st_ino)
            if key in visited_dirs:
                continue
            visited_dirs.add(key)
        except OSError:
            continue

        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=True):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=True):
                            yield entry.path
                    except OSError:
                        continue
        except OSError:
            continue


def audit(cache_path: str):
    if not os.path.isdir(cache_path):
        print(f"Error: {cache_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    binaries = []
    for path in _iter_cache_files(cache_path):
        name = os.path.basename(path)
        if name == "_main":
            continue
        rel_path = os.path.relpath(path, start=cache_path)
        print(f"Processing {rel_path}", file=sys.stderr);

        if is_elf(path):
            binaries.append(rel_path)
        elif is_zip(path) and _is_jar(name):
            # JAR files are ZIP archives, but the task treats them as binaries.
            binaries.append(rel_path)
        elif is_archive(path):
            if archive_contains_binary(path):
                binaries.append(rel_path)

    binaries.sort()
    print("Binaries")
    print("--------")
    for binary in binaries:
        print(binary)


def main():
    parser = argparse.ArgumentParser(description="Audit Bazel external cache for binary files.")
    parser.add_argument(
        "--cache",
        default="vendor/bazel-external",
        help="Path to the Bazel external cache directory (default: vendor/bazel-external)",
    )
    args = parser.parse_args()
    audit(args.cache)


if __name__ == "__main__":
    main()
