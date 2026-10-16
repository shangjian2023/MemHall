"""Governance checks must detect missing material and artifact changes."""

import importlib.util
import json
from email.message import Message
from pathlib import Path
from types import SimpleNamespace


def load_script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_document_gate_detects_broken_link(tmp_path):
    tool = load_script("check_governance")
    tool.FILES = ["README.md"]
    (tmp_path / "README.md").write_text("[Missing](missing.md)", encoding="utf-8")
    assert tool.check(tmp_path) == ["Broken local link in README.md: missing.md"]


def test_inventory_resolves_extras_and_checks_locked_versions(tmp_path, monkeypatch):
    tool = load_script("dependency_inventory")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["example[demo]>=1"]\n', encoding="utf-8"
    )
    lock = '[[package]]\nname="example"\nversion="1.0"\n[[package]]\nname="child"\nversion="2.0"\n'
    (tmp_path / "uv.lock").write_text(lock, encoding="utf-8")
    message = Message()
    message["License"] = "MIT test license text"
    distributions = {
        "example": SimpleNamespace(
            version="1.0",
            metadata=message,
            files=[],
            requires=['child==2.0; extra == "demo"', 'unused; extra == "other"'],
        ),
        "child": SimpleNamespace(version="2.0", metadata=message, files=[], requires=[]),
    }
    monkeypatch.setattr(tool.metadata, "distribution", lambda name: distributions[name])
    result = tool.collect(tmp_path / "out", tmp_path)
    assert not result["errors"]
    assert [row["name"] for row in result["components"]] == ["child", "example"]
    assert (tmp_path / "out/licenses/example/LICENSE-METADATA.txt").is_file()
    distributions["child"].version = "3.0"
    result = tool.collect(tmp_path / "out2", tmp_path)
    assert any("absent from uv.lock" in error for error in result["errors"])
    assert any("violates requirement" in error for error in result["errors"])


def test_inventory_missing_dependency_fails_check(tmp_path, monkeypatch):
    tool = load_script("dependency_inventory")
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies=["absent"]\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text("package=[]\n", encoding="utf-8")

    def missing(_):
        raise tool.metadata.PackageNotFoundError

    monkeypatch.setattr(tool.metadata, "distribution", missing)
    assert tool.collect(tmp_path / "out", tmp_path)["errors"] == [
        "Missing installed runtime dependency: absent"
    ]


def test_release_verification_detects_modified_and_missing_artifact(tmp_path):
    tool = load_script("release_checksums")
    artifact = tmp_path / "package.bin"
    artifact.write_bytes(b"original")
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "name": artifact.name,
                        "relative_path": artifact.name,
                        "bytes": artifact.stat().st_size,
                        "sha256": tool.digest(artifact),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert tool.verify(manifest) == []
    artifact.write_bytes(b"modified")
    assert tool.verify(manifest) == [artifact.name]
    artifact.unlink()
    assert tool.verify(manifest) == [artifact.name]
