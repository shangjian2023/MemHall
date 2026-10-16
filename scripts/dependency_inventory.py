"""Inventory installed runtime dependencies and preserve available license texts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import platform
import re
import sys
import tomllib
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def collect(output: Path, root: Path = ROOT) -> dict:
    project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
    lock = tomllib.loads((root / "uv.lock").read_text("utf-8"))
    locked: dict[str, set[str]] = {}
    for package in lock["package"]:
        locked.setdefault(canonicalize_name(package["name"]), set()).add(package["version"])
    queue = [(line, set()) for line in project["project"]["dependencies"]]
    seen: set[tuple[str, tuple[str, ...]]] = set()
    rows: dict[str, dict] = {}
    errors: list[str] = []
    environment = default_environment()
    while queue:
        line, parent_extras = queue.pop(0)
        req = Requirement(line)
        if req.marker and not any(
            req.marker.evaluate({**environment, "extra": extra}) for extra in parent_extras | {""}
        ):
            continue
        name = canonicalize_name(req.name)
        key = (name, tuple(sorted(req.extras)))
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = metadata.distribution(req.name)
        except metadata.PackageNotFoundError:
            errors.append(f"Missing installed runtime dependency: {name}")
            continue
        if dist.version not in locked.get(name, set()):
            errors.append(f"Installed version absent from uv.lock: {name}=={dist.version}")
        if req.specifier and not req.specifier.contains(dist.version, prereleases=True):
            errors.append(f"Installed version violates requirement: {line} (installed {dist.version})")
        if name not in rows:
            license_files = []
            declared = dist.metadata.get_all("License-File") or []
            for item in dist.files or []:
                normalized = str(item).replace("\\", "/")
                if ".dist-info/" not in normalized:
                    continue
                basename = Path(normalized).name
                if not (
                    re.match(r"(?i)^(licen[sc]e|copying|notice|authors)(\W|$)", basename)
                    or "/licenses/" in normalized
                    or any(normalized.endswith(x) for x in declared)
                ):
                    continue
                source = Path(dist.locate_file(item))
                if not source.is_file():
                    continue
                # Retain nested paths: upstream licenses often use identical filenames.
                suffix = normalized.split(".dist-info/", 1)[1]
                relative = Path("licenses") / name / suffix
                if ".." in relative.parts:
                    errors.append(f"Unsafe license path: {name}")
                    continue
                dest = output / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                content = source.read_bytes()
                dest.write_bytes(content)
                license_files.append(
                    {"path": relative.as_posix(), "sha256": hashlib.sha256(content).hexdigest()}
                )
            classifiers = [c for c in dist.metadata.get_all("Classifier") or [] if c.startswith("License ::")]
            expression = dist.metadata.get("License-Expression", "")
            license_text = dist.metadata.get("License", "")
            if not license_files and license_text and license_text.upper() != "UNKNOWN":
                relative = Path("licenses") / name / "LICENSE-METADATA.txt"
                dest = output / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(license_text, encoding="utf-8")
                license_files.append(
                    {"path": relative.as_posix(), "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()}
                )
            if not (expression or license_text or classifiers or license_files):
                errors.append(f"No license information found: {name}")
            rows[name] = {
                "name": name,
                "version": dist.version,
                "license_expression": expression,
                "license_metadata": license_text,
                "license_classifiers": classifiers,
                "license_files": license_files,
                "manual_obligations_review_required": True,
            }
        queue.extend((child, set(req.extras)) for child in dist.requires or [])
    output.mkdir(parents=True, exist_ok=True)
    result = {
        "scope": "installed Python runtime dependencies; excludes native system libraries and build tools",
        "platform": sys.platform,
        "python": platform.python_version(),
        "lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        "components": sorted(rows.values(), key=lambda r: r["name"]),
        "errors": sorted(set(errors)),
        "legal_compatibility_audit": False,
    }
    (output / "dependency-inventory.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    lines = [
        "# Third-party runtime dependency notices",
        "",
        result["scope"],
        "",
        "License metadata collection does not replace review of distribution obligations.",
        "",
        "| Component | Version | Declared license | License files |",
        "|---|---|---|---|",
    ]
    for row in result["components"]:
        label = row["license_expression"] or "; ".join(row["license_classifiers"])
        label = label or row["license_metadata"].split("\n")[0][:140] or "See upstream license files"
        links = ", ".join(f"[{Path(f['path']).name}]({f['path']})" for f in row["license_files"])
        lines.append(f"| {row['name']} | {row['version']} | {label.replace('|', '/')} | {links} |")
    (output / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines) + "\n", "utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/compliance")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = collect(args.output)
    print(
        json.dumps(
            {"components": len(result["components"]), "errors": result["errors"], "output": str(args.output)},
            ensure_ascii=True,
        )
    )
    return int(args.check and bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
