"""Optional auditd fallback for remote agent filesystem actions."""

from __future__ import annotations

import contextlib
import os
import re
import time
from collections import defaultdict
from datetime import UTC, datetime

from memhall.adapters.remote import SshChannel
from memhall.schema.evidence import Action, ActionSource

_EVENT = re.compile(r"msg=audit\((?P<ts>\d+(?:\.\d+)?):(?P<serial>\d+)\)")
_FIELD = re.compile(r"""(?P<key>\w+)=(?:"(?P<quoted>[^"]*)"|(?P<plain>\S+))""")


def _fields(line: str) -> dict[str, str]:
    return {
        match.group("key"): (match.group("quoted")
                             if match.group("quoted") is not None
                             else match.group("plain"))
        for match in _FIELD.finditer(line)
    }


def parse_audit_events(raw: str) -> list[Action]:
    """Convert ausearch records into coarse, honest filesystem actions."""
    events: dict[str, dict] = defaultdict(
        lambda: {"ts": None, "syscall": "", "exe": "", "success": "",
                 "paths": []})
    for line in raw.splitlines():
        event_match = _EVENT.search(line)
        if event_match is None:
            continue
        serial = event_match.group("serial")
        event = events[serial]
        event["ts"] = float(event_match.group("ts"))
        fields = _fields(line)
        if line.startswith("type=SYSCALL"):
            event["syscall"] = fields.get("syscall", "")
            event["exe"] = fields.get("exe", "")
            event["success"] = fields.get("success", "")
        elif line.startswith("type=PATH"):
            name = fields.get("name")
            if name:
                event["paths"].append({
                    "path": name,
                    "nametype": fields.get("nametype", ""),
                })

    actions: list[Action] = []
    for serial, event in sorted(
            events.items(), key=lambda item: int(item[0])):
        if not event["paths"]:
            continue
        syscall = str(event["syscall"])
        name_types = {item["nametype"] for item in event["paths"]}
        if (syscall in {"rename", "renameat", "renameat2"}
                or {"CREATE", "DELETE"} <= name_types):
            tool = "fs.rename"
        elif "DELETE" in name_types or syscall in {"unlink", "unlinkat", "rmdir"}:
            tool = "fs.delete"
        elif "CREATE" in name_types or syscall in {"mkdir", "mkdirat", "creat"}:
            tool = "fs.create"
        else:
            tool = "fs.write"
        timestamp = datetime.fromtimestamp(
            float(event["ts"] or 0), tz=UTC)
        actions.append(Action(
            action_id=f"audit-{serial}",
            ts=timestamp,
            tool=tool,
            args={
                "paths": event["paths"],
                "syscall": syscall,
                "exe": event["exe"],
            },
            result=("ok" if event["success"] in {"yes", "1"} else
                    event["success"] or None),
            source=ActionSource.AUDITD,
        ))
    return actions


class AuditdCollector:
    """Manage one uniquely keyed audit watch on the remote user's home."""

    def __init__(self, channel: SshChannel):
        self.channel = channel
        self.enabled = os.environ.get("MEMHALL_AUDITD", "0") == "1"
        self.active = False
        self.home = f"/home/{channel.user}"
        self.key = ""
        self.error: str | None = None

    def start(self) -> None:
        self.stop()
        self.error = None
        if not self.enabled:
            return
        self.key = f"mh_{int(time.time())}_{os.getpid()}"[:30]
        command = (
            "command -v auditctl >/dev/null && command -v ausearch >/dev/null && "
            f"auditctl -w {self.home} -p wa -k {self.key}"
        )
        try:
            rc, _, err = self.channel.sudo(command, timeout=30)
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}"
            return
        if rc != 0:
            self.error = err.strip()[:300] or f"auditctl rc={rc}"
            return
        self.active = True

    def dump(self) -> list[Action]:
        if not self.active:
            return []
        try:
            rc, out, err = self.channel.sudo(
                f"ausearch -if /var/log/audit/audit.log -k {self.key} --raw",
                timeout=60,
            )
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}"
            return []
        if rc != 0:
            self.error = err.strip()[:300] or f"ausearch rc={rc}"
            return []
        return parse_audit_events(out)

    def stop(self) -> None:
        if not self.active:
            return
        # 清理 audit 规则尽力而为：评测中途挂了也别让 -W 失败打断收尾
        with contextlib.suppress(Exception):
            self.channel.sudo(
                f"auditctl -W {self.home} -p wa -k {self.key} "
                ">/dev/null 2>&1 || true",
                timeout=30,
            )
        self.active = False
