"""auditd 解析、环境指纹与 VMware 命令边界测试
（择优移植自 leeyu44 PR #3；fork 专属的拨钟/reboot/fs-probe 用例未随移植，
主线等价能力由 systest 相关测试覆盖）。"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from memhall.adapters.audit import AuditdCollector, parse_audit_events
from memhall.adapters.remote import remote_environment
from memhall.vm import VmwareManager


def test_parse_audit_events_maps_create_and_delete():
    raw = """+type=SYSCALL msg=audit(1791072000.125:41): syscall=mkdir success=yes exe="/usr/bin/mkdir"
type=PATH msg=audit(1791072000.125:41): name="/home/okim/work/demo" nametype=CREATE
----
type=SYSCALL msg=audit(1791072001.500:42): syscall=unlinkat success=yes exe="/usr/bin/rm"
type=PATH msg=audit(1791072001.500:42): name="/home/okim/work/old" nametype=DELETE
----
type=SYSCALL msg=audit(1791072002.750:43): syscall=renameat2 success=yes exe="/usr/bin/mv"
type=PATH msg=audit(1791072002.750:43): name="/home/okim/work/before" nametype=DELETE
type=PATH msg=audit(1791072002.750:43): name="/home/okim/work/after" nametype=CREATE
"""
    actions = parse_audit_events(raw)
    assert [item.tool for item in actions] == [
        "fs.create", "fs.delete", "fs.rename"]
    assert actions[0].args["paths"][0]["path"] == "/home/okim/work/demo"
    assert all(item.source.value == "auditd" for item in actions)


def test_audit_collector_reads_explicit_openkylin_log(monkeypatch):
    class Channel:
        user = "kylin"

        def __init__(self):
            self.commands = []

        def sudo(self, command, timeout=300):
            self.commands.append(command)
            return 0, "", ""

    monkeypatch.setenv("MEMHALL_AUDITD", "1")
    channel = Channel()
    collector = AuditdCollector(channel)
    collector.active = True
    collector.key = "mh_test"
    collector.dump()
    assert channel.commands == [
        "ausearch -if /var/log/audit/audit.log -k mh_test --raw"]


def test_remote_environment_records_measured_agent_version(monkeypatch):
    class Channel:
        def run_json(self, command, timeout=300):
            assert command.endswith("| base64 -d | python3")
            assert timeout == 30
            source = base64.b64decode(command.split()[1]).decode("utf-8")
            compile(source, "<remote-environment>", "exec")
            return {
                "kind": "remote",
                "os": "openKylin 3.0",
                "os_id": "openkylin",
            }

        def run(self, command, timeout=300):
            assert command == "kylin-bot --version 2>/dev/null"
            return 0, "kylin-bot 0.7.5\n", ""

        def host_key_sha256(self):
            return "host-key"

    monkeypatch.setenv("VM_SNAPSHOT", "agents-warm")
    info = remote_environment(
        Channel(), "kylinbot", "kylin-bot --version 2>/dev/null")
    assert info["agent_version"] == "kylin-bot 0.7.5"
    assert info["adapter"] == "kylinbot"
    assert info["vm_snapshot"] == "agents-warm"
    assert info["ssh_host_key_sha256"] == "host-key"


def test_vm_manager_uses_argument_list_and_revert_sequence(
        tmp_path: Path, monkeypatch):
    vmx = tmp_path / "open kylin.vmx"
    vmrun = tmp_path / "vmrun.exe"
    vmx.write_text("config", encoding="utf-8")
    vmrun.write_text("binary", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        operation = command[3]
        if operation == "listSnapshots":
            output = "Total snapshots: 1\nagents-warm\n"
        elif operation == "list":
            output = "Total running VMs: 0\n"
        else:
            output = ""
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manager = VmwareManager(vmx, "agents-warm", vmrun)
    monkeypatch.setattr(manager, "start", lambda **kwargs: calls.append(["start-wait"]))
    manager.revert()
    revert = next(call for call in calls if "revertToSnapshot" in call)
    assert revert[-2:] == [str(vmx.resolve()), "agents-warm"]
    assert calls[-1] == ["start-wait"]


def test_deb_acceptance_script_uses_mainline_cli():
    root = Path(__file__).parent.parent
    acceptance = (root / "scripts" / "test_deb_vm.sh").read_text(encoding="utf-8")
    assert "set -Eeuo pipefail" in acceptance
    assert "memhall run" in acceptance          # 主线两次独立 run 替代 fork 的 --repeat
    assert "memhall verify" in acceptance
    assert "memhall stability" in acceptance
    assert "-name manifest.json" in acceptance
    assert "build.json" in acceptance and "deb_sha256" in acceptance
