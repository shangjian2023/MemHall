"""Bounded read-only fingerprints of files already included in an adapter snapshot."""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024


def local_hashes(paths: list[str], root: Path) -> dict[str, str]:
    hashes = {}
    root = root.resolve()
    for name in paths:
        path = root / name.removeprefix("~/")
        try:
            if (
                path.is_file()
                and not path.is_symlink()
                and path.resolve().is_relative_to(root)
                and path.stat().st_size <= MAX_BYTES
            ):
                hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return hashes


def remote_hashes(paths: list[str], channel) -> dict[str, str] | None:
    script = """import hashlib,json,pathlib,sys
out={}
for name in json.load(sys.stdin):
 p=pathlib.Path(name).expanduser()
 try:
  if p.is_file() and not p.is_symlink() and p.stat().st_size <= 2097152:
   out[name]=hashlib.sha256(p.read_bytes()).hexdigest()
 except OSError: pass
print(json.dumps(out))
"""
    rc, stdout, _ = channel.run("python3 -c " + shlex.quote(script), timeout=60, stdin_data=json.dumps(paths))
    if rc:
        return None
    try:
        result = json.loads(stdout)
        return result if isinstance(result, dict) else None
    except ValueError:
        return None
