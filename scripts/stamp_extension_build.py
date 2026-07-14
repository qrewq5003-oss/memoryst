"""
Stamp the current git commit into the SillyTavern extension and cache-bust its imports.

The browser caches every ES module independently, and a mobile browser has no hard-reload
gesture. During the stage-D live test this repeatedly produced the worst possible debugging
state: index.js fresh, trackers.mjs stale, and an audit record that described the behaviour
of code that no longer existed. Half a day went into chasing bugs that were already fixed
on disk.

So each import of a local module carries the build in its URL - `./trackers.mjs?v=<sha>` -
which makes a stale module impossible: a new build changes every URL, and the browser has
nothing cached under it.

    python -m scripts.stamp_extension_build          # stamp HEAD
    python -m scripts.stamp_extension_build --check  # fail if the stamp is out of date
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

EXTENSION_DIR = Path(__file__).resolve().parent.parent / "sillytavern-extension"
LOCAL_IMPORT_RE = re.compile(r"(from\s+'\./[A-Za-z0-9_.-]+\.mjs)(\?v=[0-9a-f]+)?(')")
BUILD_CONST_RE = re.compile(r"(export const MEMORY_EXTENSION_BUILD = ')[^']*(')")


def current_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()


def stamp(sha: str, check_only: bool = False) -> bool:
    changed = False

    for path in sorted(EXTENSION_DIR.glob("*.mjs")) + sorted(EXTENSION_DIR.glob("*.js")):
        original = path.read_text()
        updated = LOCAL_IMPORT_RE.sub(rf"\1?v={sha}\3", original)

        if path.name == "version.mjs":
            updated = BUILD_CONST_RE.sub(rf"\g<1>{sha}\g<2>", updated)

        if updated != original:
            changed = True
            if not check_only:
                path.write_text(updated)
                print(f"  stamped {path.name}")

    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="exit 1 if the stamp is stale")
    args = parser.parse_args()

    sha = current_sha()
    changed = stamp(sha, check_only=args.check)

    if args.check and changed:
        print(f"extension build stamp is stale (HEAD is {sha}); run scripts/stamp_extension_build.py")
        sys.exit(1)

    print(f"extension build: {sha}" + ("" if changed else " (already current)"))


if __name__ == "__main__":
    main()
