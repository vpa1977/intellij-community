#!/usr/bin/env python3
"""Materialize a Bazel vendor directory by replacing symlinks with actual files.

Usage:
    materialize_cache.py <vendor> <new-vendor>

All files and directories are copied from <vendor> to <new-vendor>. Symlinks
are resolved and replaced with the actual files or directories they point to.
"""

import argparse
import os
import shutil
import stat
import sys


def _copy_stat(src: str, dst: str) -> None:
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass


def materialize(src: str, dst: str, visited: set[str] | None = None) -> None:
    if visited is None:
        visited = set()

    real_src = os.path.realpath(src)
    if real_src in visited:
        return  # Cycle detected.

    try:
        st = os.lstat(src)
    except OSError as e:
        print(f"Warning: cannot stat {src}: {e}", file=sys.stderr)
        return

    if stat.S_ISLNK(st.st_mode):
        # Resolve the symlink and materialize its target.
        if os.path.isdir(real_src):
            os.makedirs(dst, exist_ok=True)
            _copy_stat(real_src, dst)
            try:
                entries = os.listdir(real_src)
            except OSError as e:
                print(f"Warning: cannot list {real_src}: {e}", file=sys.stderr)
                return
            for entry in entries:
                materialize(os.path.join(real_src, entry), os.path.join(dst, entry), visited | {real_src})
        elif os.path.isfile(real_src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.copy2(real_src, dst)
            except OSError as e:
                print(f"Warning: cannot copy {real_src} to {dst}: {e}", file=sys.stderr)
        else:
            print(f"Warning: skipping unsupported symlink target {real_src}", file=sys.stderr)
    elif os.path.isdir(src):
        os.makedirs(dst, exist_ok=True)
        _copy_stat(src, dst)
        try:
            entries = os.listdir(src)
        except OSError as e:
            print(f"Warning: cannot list {src}: {e}", file=sys.stderr)
            return
        for entry in entries:
            materialize(os.path.join(src, entry), os.path.join(dst, entry), visited | {real_src})
    elif os.path.isfile(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except OSError as e:
            print(f"Warning: cannot copy {src} to {dst}: {e}", file=sys.stderr)
    else:
        print(f"Warning: skipping unsupported file type {src}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Materialize symlinks in a Bazel vendor directory.")
    parser.add_argument("vendor", help="Source vendor directory")
    parser.add_argument("new_vendor", help="Destination vendor directory")
    args = parser.parse_args()

    if not os.path.isdir(args.vendor):
        print(f"Error: {args.vendor} is not a directory", file=sys.stderr)
        sys.exit(1)

    materialize(args.vendor, args.new_vendor)
    print(f"Materialized vendor directory written to {args.new_vendor}")


if __name__ == "__main__":
    main()
