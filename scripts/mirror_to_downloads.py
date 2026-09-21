#!/usr/bin/env python3
"""Mirror the project into Android's Downloads folder for easy file-manager
browsing on the phone. Read-only convenience copy - never run the server from
here, and never treat it as a git checkout (its .git is deliberately skipped).

rsync isn't available in this Termux sandbox (no network access to fetch it),
so this reimplements the subset of `rsync -a --delete` behaviour needed here:
copy new/changed files, and remove from the destination anything that vanished from
the source *or* is excluded from the mirror.

That last part is a fix, not the original behaviour. This used to skip excluded names on
the destination side too, on the theory that it was protecting something deliberately
created there. What it actually did was strand anything copied before an exclusion was
added: the copy loop stopped refreshing it and the delete loop refused to remove it.

Found 2026-09-21. `/storage/emulated/0/Download/memoryst/.env` had been sitting on Android
shared storage - readable by any app holding the storage permission - since 2026-06-28,
holding a Google API key that was still live when tested. Alongside it: a copy of
memory.db, a chromadb directory, and 670 MB of .venv/.mimocode. Of the mirror's 736 MB,
about 3 MB was the source it exists to show.

A mirror that keeps what it excludes is not a mirror, and the hypothetical it protected
was never worth a secret in a world-readable directory.
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
        # Excluded names are deleted here rather than skipped: whatever is excluded from
        # the mirror has no business surviving in it. See the module docstring.
        if name not in src_entries or is_excluded_name(name):
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
