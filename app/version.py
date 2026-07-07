"""Version and compatibility metadata for the memory service.

The `/memory/version` endpoint exists so the SillyTavern extension can detect
when it is talking to an incompatible backend. The failure mode this guards
against: a SillyTavern reinstall or git update silently recreates `public/`
and drops the symlink to `sillytavern-extension/`, leaving a *stale* copy of
the extension running against an *updated* backend, with no signal to the user.

The extension is static JS shipped inside `public/`, so it cannot know which
git commit it was built from at runtime. The only thing it can reliably
self-report is a hand-maintained integer contract version. That is
`PROTOCOL_VERSION` below: bump it whenever the `/memory/store` or
`/memory/retrieve` request/response contract changes in a way that would break
an older extension (or an older backend against a newer extension). The
extension hard-codes the same number and compares. `git_commit` and
`service_version` are reported alongside purely as human-readable diagnostics
shown in the UI - they are not what drives the compatibility warning.
"""

from pathlib import Path

# Human-facing release version of the backend service. Independent of the
# extension's manifest version - they live in separate version spaces.
SERVICE_VERSION = "0.1.0"

# Contract version shared with the extension (see module docstring). Bump ONLY
# on a breaking change to the /memory/store or /memory/retrieve contract, and
# bump MEMORY_PROTOCOL_VERSION in sillytavern-extension/version.mjs to match.
PROTOCOL_VERSION = 1

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_git_dir(repo_root: Path) -> Path | None:
    """Return the effective .git directory, following the `gitdir:` pointer
    used by worktrees (where `.git` is a file, not a directory)."""
    git_path = repo_root / ".git"
    if git_path.is_dir():
        return git_path
    if git_path.is_file():
        # Worktree / submodule: `.git` is a file containing `gitdir: <path>`.
        try:
            content = git_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if content.startswith("gitdir:"):
            target = content[len("gitdir:"):].strip()
            resolved = (repo_root / target).resolve() if not Path(target).is_absolute() else Path(target)
            return resolved if resolved.exists() else None
    return None


def _read_git_commit(repo_root: Path = _REPO_ROOT) -> str | None:
    """Best-effort short git commit for the running backend.

    Reads .git directly rather than shelling out to `git`, so it works without
    the git binary and never raises - a diagnostic string that is simply
    absent when it cannot be determined. Returns None when not in a git
    checkout (e.g. deployed as a plain file copy).
    """
    git_dir = _resolve_git_dir(repo_root)
    if git_dir is None:
        return None

    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not head.startswith("ref:"):
        # Detached HEAD: HEAD holds the commit SHA directly.
        return head[:12] or None

    ref = head[len("ref:"):].strip()

    # Loose ref: <git_dir>/refs/heads/<branch>
    loose_ref = git_dir / ref
    try:
        return loose_ref.read_text(encoding="utf-8").strip()[:12] or None
    except OSError:
        pass

    # Packed ref fallback: <git_dir>/packed-refs, lines of "<sha> <ref>".
    packed = git_dir / "packed-refs"
    try:
        for line in packed.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "^")):
                continue
            sha, _, name = line.partition(" ")
            if name == ref:
                return sha[:12] or None
    except OSError:
        pass

    return None


def get_version_info() -> dict:
    """Version payload served by GET /memory/version."""
    return {
        "service_version": SERVICE_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "git_commit": _read_git_commit(),
    }
