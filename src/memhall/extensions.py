"""Record implementations used by scoring, including explicitly selected plugins."""
from __future__ import annotations

import hashlib
import inspect
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from memhall.schema.models_case import MemoryCase
from memhall.scoring.rules import ASSERTS


def _stamp(obj) -> dict:
    module = obj.__module__
    try:
        package_version = version(module.split(".")[0])
    except PackageNotFoundError:
        package_version = None
    try:
        source_hash = hashlib.sha256(inspect.getsource(obj).encode("utf-8")).hexdigest()
    except (OSError, TypeError):
        source_hash = None
    return {"implementation": module + ":" + obj.__qualname__,
            "package_version": package_version, "source_sha256": source_hash}


def scoring_implementation(cases: list[MemoryCase]) -> dict:
    builtins = {}
    for name in ("metrics", "rules", "judge", "engine"):
        module = import_module("memhall.report.metrics" if name == "metrics" else "memhall.scoring." + name)
        path = Path(module.__file__ or "")
        builtins[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    names = {b.assert_name for c in cases for p in c.probes if p.kind == "rule" for b in p.check}
    plugins = {name: _stamp(ASSERTS[name]) for name in sorted(names)
               if name in ASSERTS and ASSERTS[name].__module__ != "memhall.scoring.rules"}
    return {"builtin_source_hashes": builtins, "assertion_plugins": plugins}
