#!/usr/bin/env python3
"""Mirror the project into Android's Downloads folder for easy file-manager
browsing on the phone. Read-only convenience copy - never run the server from
here, and never treat it as a git checkout (its .git is deliberately skipped).

rsync isn't available in this Termux sandbox (no network access to fetch it),
so this reimplements the subset of `rsync -a --delete` behaviour needed here:
copy new/changed files, remove files/dirs that vanished from the source, and
leave alone anything under an excluded name on the destination side (e.g. its
own database, if one were ever created there by mistake).
"""
import os
import shutil
import sys

SRC = "/data/data/com.termux/files/home/memoryst"
DST = "/storage/emulated/0/Download/memoryst"

EXCLUDE_DIRS = {".venv", "venv", ".git", "data", ".pytest_cache", ".mimocode", "__pycache__"}
EXCLUDE_FILES = {".env", ".env.save"}
EXCLUDE_SUFFIXES = (".pyc",)


def is_excluded_name(name: str) -> bool:
    return name in EXCLUDE_DIRS or name in EXCLUDE_FILES or name.endswith(EXCLUDE_SUFFIXES)


def sync(src: str, dst: str) -> None:
    os.makedirs(dst, exist_ok=True)
    src_entries = {e.name: e for e in os.scandir(src)}
    dst_entries = {e.name: e for e in os.scandir(dst)}

    for name, entry in dst_entries.items():
        if is_excluded_name(name):
            continue
        if name not in src_entries:
            if entry.is_dir():
                shutil.rmtree(entry.path)
            else:
                os.remove(entry.path)

    for name, entry in src_entries.items():
        if is_excluded_name(name):
            continue
        dst_path = os.path.join(dst, name)
        if entry.is_dir():
            sync(entry.path, dst_path)
        else:
            if (
                not os.path.exists(dst_path)
                or os.path.getmtime(entry.path) > os.path.getmtime(dst_path)
                or os.path.getsize(entry.path) != os.path.getsize(dst_path)
            ):
                shutil.copy2(entry.path, dst_path)


if __name__ == "__main__":
    sync(SRC, DST)
    print(f"mirror sync complete: {SRC} -> {DST}", file=sys.stderr)
