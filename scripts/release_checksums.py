"""Generate and verify release artifact hashes without modifying artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def verify(manifest: Path) -> list[str]:
    data = json.loads(manifest.read_text("utf-8"))
    errors = []
    for item in data["artifacts"]:
        path = (manifest.parent / item["relative_path"]).resolve()
        if not path.is_file() or digest(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            errors.append(item["name"])
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "build/release-checks")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        errors = verify(args.verify)
        print(json.dumps({"verified": not errors, "failed_artifacts": errors}))
        return int(bool(errors))
    if not args.artifacts:
        parser.error("At least one artifact is required")
    names = [p.name for p in args.artifacts]
    if len(set(names)) != len(names):
        parser.error("Artifact basenames must be unique")
    # Relative paths allow verification on the build machine without embedding user paths.
    import os

    artifacts = [
        {
            "name": p.name,
            "relative_path": os.path.relpath(p.resolve(), args.output.resolve()),
            "bytes": p.stat().st_size,
            "sha256": digest(p),
        }
        for p in args.artifacts
    ]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
    )
    files = [
        {"path": p.relative_to(ROOT).as_posix(), "sha256": digest(p)}
        for p in sorted((ROOT / "src/memhall").rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]
    result = {
        "git_baseline": commit,
        "tracked_worktree_modified": bool(status.strip()),
        "runtime_source_files": files,
        "artifacts": artifacts,
        "path_scope": "relative_path is local build layout; SHA256SUMS.txt uses portable asset basenames",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "release-manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    (args.output / "SHA256SUMS.txt").write_text(
        "".join(f"{r['sha256']}  {r['name']}\n" for r in artifacts), "utf-8"
    )
    print(json.dumps({"artifacts": len(artifacts), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
