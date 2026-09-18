#!/usr/bin/env python3
"""Keep the skeletonconverter skill in sync with its upstream repo.

Self-contained: copy this file + SKILL.md + validation/ anywhere and the skill
can update itself. Checks the upstream repo HEAD at most once per TTL window
so loading the skill stays cheap. --force ignores the window; --check reports
drift without writing.

Usage:
    python3 update.py            # sync if upstream changed (TTL window)
    python3 update.py --force    # sync ignoring TTL
    python3 update.py --check    # report drift, write nothing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = "https://github.com/JonathanReisCom/skeletonconverter.git"
SKILL_NAME = "skeletonconverter"
SELF = Path(__file__).resolve()
TTL_SECONDS = 24 * 60 * 60

# Files that make up the skill, relative to the upstream checkout. update.py
# must re-copy itself so the installed skill can keep updating itself.
SKILL_FILES = [
    "skill/SKILL.md",
    "validation/pose-sample-spine.mjs",
    "validation/pose-sample-godot.gd",
    "validation/validate-roundtrip.sh",
    "update.py",
    "LICENSE",
]

LOCK_NAME = ".skill-lock.json"

PRELUDE = """
## Before you start: refresh this skill

```bash
python3 "{SKILL_DIR}/update.py"
```

Checks upstream at most once per day (no-op otherwise, ~1s). Start only after
it exits 0.
"""


def skill_dir() -> Path:
    """Resolve the skill directory whether run from the repo or installed."""
    # Running from the skill copy (e.g. inside an OMP plugin cache)
    if SELF.parent.name == SKILL_NAME:
        return SELF.parent
    # Running from the repo itself
    return SELF.parent


def run(cmd: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def remote_head() -> str:
    """Ask upstream for its HEAD hash without cloning (~2s, no working tree)."""
    out = run(["git", "ls-remote", REPO, "HEAD"])
    return out.split()[0] if out else ""


def shallow_clone(dest: Path) -> str:
    """Shallow-clone upstream and return the resolved commit hash."""
    run(["git", "clone", "--depth", "1", REPO, str(dest)])
    return run(["git", "rev-parse", "HEAD"], cwd=dest)


def collect(src: Path) -> dict:
    """Map each skill file relative to the skill directory."""
    wanted = {}
    for rel in SKILL_FILES:
        src_file = src / rel
        if src_file.exists():
            wanted[src_file] = Path(rel)
    return wanted


def install(src: Path, files: dict, target: Path) -> list[str]:
    """Write files into the skill directory. Returns sorted names."""
    written = []
    target.mkdir(parents=True, exist_ok=True)
    for src_file, rel in files.items():
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src_file.read_bytes())
        written.append(str(rel))
    return sorted(written)


def inject_prelude(skill_md: Path, target: Path) -> None:
    """Add the refresh instruction after the YAML frontmatter, once."""
    text = skill_md.read_text(encoding="utf-8")
    if "refresh this skill" in text:
        return
    block = PRELUDE.replace("{SKILL_DIR}", str(target))
    # Insert after the closing --- of the YAML frontmatter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end >= 0:
            skill_md.write_text(
                text[:end + 3] + "\n" + block + "\n" + text[end + 3:],
                encoding="utf-8",
            )
            return
    skill_md.write_text(block + "\n" + text, encoding="utf-8")


def fingerprint(target: Path, files: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(files):
        content = (target / rel).read_bytes()
        digest.update(rel.encode())
        digest.update(content)
    return digest.hexdigest()


def read_lock(lock_path: Path) -> dict:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_lock(lock_path: Path, head: str, files: list[str], fp: str) -> None:
    lock_path.write_text(json.dumps({
        "repo": REPO,
        "commit": head,
        "files": files,
        "fingerprint": fp,
    }, indent=2) + "\n", encoding="utf-8")


def check(force: bool) -> int:
    target = skill_dir()
    lock_path = target / LOCK_NAME
    lock = read_lock(lock_path)

    if not force:
        last_check = lock.get("checked_at", 0)
        import time
        if time.time() - last_check < TTL_SECONDS:
            print("skeletonconverter: up to date (within TTL window)")
            return 0

    head = remote_head()
    if not head:
        print("skeletonconverter: could not reach upstream")
        return 1
    if head == lock.get("commit"):
        print("skeletonconverter: up to date")
        return 0

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "upstream"
        commit = shallow_clone(src)
        files = collect(src)
        written = install(src, files, target)
        skill_md = target / "SKILL.md"
        if skill_md.exists():
            inject_prelude(skill_md, target)
        fp = fingerprint(target, written)
        write_lock(lock_path, commit, written, fp)
        print(f"skeletonconverter: updated to {commit[:8]} ({len(written)} files)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="ignore the TTL window and check upstream now")
    parser.add_argument("--check", action="store_true",
                        help="report drift without writing")
    args = parser.parse_args()

    if args.check:
        head = remote_head()
        lock = read_lock(skill_dir() / LOCK_NAME)
        if head == lock.get("commit"):
            print("up to date")
        else:
            print(f"upstream at {head[:8]}, local at {lock.get('commit', '?')[:8]} — run update.py to sync")
        return 0

    return check(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
