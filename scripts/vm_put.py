"""向 openKylin 虚拟机推送文件（paramiko SFTP）。

用法：VM_PASS=xxx uv run python scripts/vm_put.py <本地文件> <远端路径>
"""

from __future__ import annotations

import os
import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "192.168.61.133"
USER = "okim"


def main() -> int:
    if len(sys.argv) != 3 or "VM_PASS" not in os.environ:
        print("用法: VM_PASS=xxx uv run python scripts/vm_put.py <本地文件> <远端路径>")
        return 2
    local, remote = sys.argv[1], sys.argv[2]
    size = os.path.getsize(local)
    t0 = time.time()

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, username=USER, password=os.environ["VM_PASS"], timeout=15)
    try:
        sftp = cli.open_sftp()

        def cb(done: int, total: int) -> None:
            pct = done * 100 // total
            print(f"\r{pct:3d}%  {done//1024//1024}/{total//1024//1024} MB", end="")

        sftp.put(local, remote, callback=cb)
        print()
        rsize = sftp.stat(remote).st_size
    finally:
        cli.close()
    ok = rsize == size
    dt = time.time() - t0
    print(f"{'OK' if ok else 'SIZE-MISMATCH'}: {size//1024//1024} MB in {dt:.0f}s ({size/1024/1024/max(dt,0.1):.1f} MB/s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
