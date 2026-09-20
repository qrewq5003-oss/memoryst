"""Publish `sillytavern-extension/` to an orphan branch whose root *is* the extension.

Why a branch and not a directory move: SillyTavern's "Install Extension" clones a repo
and reads `manifest.json` from the clone root (`src/endpoints/extensions.js`,
`getManifest`). This repo's root is the backend, so the button fails outright. The three
ways out are moving the extension to the root (which buries the backend under eight
files that belong to a browser), splitting the extension into its own repo (which
separates code from the `tests/` and the protocol version it is checked against), or
publishing a branch. The install dialog has a "Branch or tag name" field, so the branch
costs nothing at install time and nothing structurally here.

Installed that way the extension lands in `<user>/extensions/memoryst` - the directory
name comes from the URL's basename, not the branch - and `src/users.js` serves it under
`/scripts/extensions/third-party/`, the same URL depth as a global install. That matters
because `main.mjs` reaches SillyTavern through `../../../extensions.js`: get the depth
wrong and every import breaks.

    python -m scripts.publish_extension_branch --check   # is the branch in sync?
    python -m scripts.publish_extension_branch           # update it locally
    python -m scripts.publish_extension_branch --push    # ...and push it

The branch is a *publication*, so it can go stale the moment the extension changes -
which is the failure this repo keeps re-learning. --check is the guard: it compares the
branch tip's tree against the current sources and says plainly which files differ.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION_DIR = REPO_ROOT / "sillytavern-extension"
BRANCH = "extension"

# Everything a browser needs and nothing else. The backend's README, the docs and the
# tests stay behind: an install pulls this branch into SillyTavern's tree, where they are
# dead weight that also goes stale invisibly. The extension's own README comes along,
# because that is the file someone reads after installing it.
PUBLISHED_SUFFIXES = {".js", ".mjs", ".json", ".css", ".md"}


def git(*args: str, cwd: Path = REPO_ROOT, check: bool = True, strip: bool = True) -> str:
    """Run git and return its stdout.

    `strip=False` matters for `git show`: file contents end in a newline, and stripping
    it made every published file compare unequal to its source - --check reported the
    whole extension as stale on a branch that had just been published from it.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if strip else result.stdout


def published_files() -> list[Path]:
    """The files that make up the published branch, relative to EXTENSION_DIR."""
    return sorted(
        path.relative_to(EXTENSION_DIR)
        for path in EXTENSION_DIR.iterdir()
        if path.is_file() and path.suffix in PUBLISHED_SUFFIXES
    )


def branch_exists() -> bool:
    return bool(git("branch", "--list", BRANCH, check=False))


def branch_file_contents() -> dict[str, str]:
    """What the branch currently publishes, as {relative path: content}."""
    if not branch_exists():
        return {}

    listing = git("ls-tree", "-r", "--name-only", BRANCH, check=False)
    contents = {}
    for name in filter(None, listing.splitlines()):
        contents[name] = git("show", f"{BRANCH}:{name}", check=False, strip=False)
    return contents


def source_file_contents() -> dict[str, str]:
    return {
        str(rel): (EXTENSION_DIR / rel).read_text(encoding="utf-8")
        for rel in published_files()
    }


def diff_against_branch() -> list[str]:
    """Names of files that differ between the sources and the published branch."""
    source = source_file_contents()
    published = branch_file_contents()
    return sorted(set(source) ^ set(published) | {
        name for name in set(source) & set(published) if source[name] != published[name]
    })


def assert_stamp_is_current() -> None:
    """Refuse to publish sources whose build stamp is stale.

    A published branch is exactly where a stale stamp does the most damage: the browser
    caches modules by URL, and the stamp is what makes a new build change those URLs.
    Publishing unstamped sources ships an extension that some users will never see
    update.
    """
    from scripts.stamp_extension_build import content_build_id, stamp

    if stamp(content_build_id(), check_only=True):
        raise SystemExit(
            "extension build stamp is stale - run scripts/stamp_extension_build.py, "
            "commit, then publish"
        )


def assert_sources_are_committed() -> None:
    """The branch must name a real commit, so what it publishes can be traced back."""
    dirty = git("status", "--porcelain", "--", str(EXTENSION_DIR), check=False)
    if dirty:
        raise SystemExit(
            "sillytavern-extension/ has uncommitted changes:\n"
            f"{dirty}\n"
            "commit them first - the published branch records which commit it came from"
        )


def publish(push: bool) -> None:
    assert_stamp_is_current()
    assert_sources_are_committed()

    source_commit = git("rev-parse", "--short", "HEAD")
    files = published_files()

    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "extension-branch"

        # A detached worktree, then a branch created inside it: this never touches the
        # checkout the user is working in, so publishing cannot disturb an edit in flight.
        if branch_exists():
            git("worktree", "add", "--quiet", str(worktree), BRANCH)
        else:
            git("worktree", "add", "--quiet", "--detach", str(worktree), "HEAD")
            git("checkout", "--quiet", "--orphan", BRANCH, cwd=worktree)
            git("rm", "-rq", "--cached", ".", cwd=worktree, check=False)
            for stray in worktree.iterdir():
                if stray.name == ".git":
                    continue
                shutil.rmtree(stray) if stray.is_dir() else stray.unlink()

        try:
            # Delete before copying: a file dropped from the extension has to disappear
            # from the branch too, and syncing by copy alone would leave it behind
            # forever.
            for existing in worktree.iterdir():
                if existing.name == ".git":
                    continue
                shutil.rmtree(existing) if existing.is_dir() else existing.unlink()

            for rel in files:
                shutil.copy2(EXTENSION_DIR / rel, worktree / rel)

            git("add", "-A", cwd=worktree)
            if not git("status", "--porcelain", cwd=worktree, check=False):
                print(f"branch '{BRANCH}' already matches {source_commit}")
            else:
                git(
                    "commit",
                    "--quiet",
                    "-m",
                    f"publish extension from {source_commit}",
                    cwd=worktree,
                )
                print(f"branch '{BRANCH}' updated from {source_commit}")

            if push:
                git("push", "--quiet", "origin", BRANCH, cwd=worktree)
                print(f"pushed '{BRANCH}' to origin")
        finally:
            git("worktree", "remove", "--force", str(worktree), check=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="exit 1 if the branch is stale")
    parser.add_argument("--push", action="store_true", help="push the branch to origin")
    args = parser.parse_args()

    if args.check:
        differences = diff_against_branch()
        if differences:
            print(f"branch '{BRANCH}' is out of date; differing files:")
            for name in differences:
                print(f"  {name}")
            print("run: python -m scripts.publish_extension_branch --push")
            sys.exit(1)
        print(f"branch '{BRANCH}' is in sync")
        return

    publish(push=args.push)


if __name__ == "__main__":
    main()
