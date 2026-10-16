"""Check governance entry points and local Markdown links in maintained policies."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "README.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "docs/maintenance.md",
    "docs/ROADMAP.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
]


def check(root: Path = ROOT) -> list[str]:
    errors = []
    for name in FILES:
        path = root / name
        if not path.is_file():
            errors.append(f"Missing required governance file: {name}")
            continue
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text("utf-8")):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            target = target.split("#", 1)[0]
            if target and not (path.parent / target).exists():
                errors.append(f"Broken local link in {name}: {target}")
    return errors


if __name__ == "__main__":
    problems = check()
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"Governance documentation check: {'FAIL' if problems else 'PASS'}")
    raise SystemExit(bool(problems))
