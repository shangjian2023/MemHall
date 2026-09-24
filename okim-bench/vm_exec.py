"""虚拟机 SSH 执行助手（开发测试机专用）。

用法：
  set VM_PASS=虚拟机密码
  python vm_exec.py "命令1" "命令2" ...

注意：密码只从环境变量读取，不写入任何文件；正式评测环境由 D 角色的 runner 接管。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_deps"))
import paramiko  # noqa: E402

HOST, PORT, USER = "127.0.0.1", 2222, "lcy"


def run(cmds: list[str], timeout: int = 60) -> list[tuple[str, str, str]]:
    """返回 [(cmd, stdout, stderr)]。"""
    password = os.environ.get("VM_PASS")
    if not password:
        raise SystemExit("请设置环境变量 VM_PASS")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=password, timeout=10)
    results = []
    try:
        for cmd in cmds:
            _, out, err = c.exec_command(cmd, timeout=timeout)
            results.append((cmd, out.read().decode("utf-8", "replace"),
                            err.read().decode("utf-8", "replace")))
    finally:
        c.close()
    return results


if __name__ == "__main__":
    for cmd, out, err in run(sys.argv[1:]):
        print(f"$ {cmd}")
        if out.strip():
            print(out.rstrip())
        if err.strip():
            print("[stderr]", err.rstrip())
        print()
