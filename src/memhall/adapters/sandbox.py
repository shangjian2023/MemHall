"""OS 级沙箱包装（roadmap B8）：被测 agent 进程的写面强制隔离。

bwrap 路线（openKylin 3.0 VM 实测 2026-10-14：kernel 7.0 + systemd 259 +
cgroup v2，bwrap 装机自带、unprivileged userns 可用，无需 root）：

- 整根文件系统只读挂载，白名单目录可写（agent 自家 dotdir + 用例注入工作区）；
- /tmp、~/.cache、~/.npm 用 tmpfs——每条消息独立、跨 case 不残留（防 agent
  把记忆性内容藏进快照与 reset 都看不见的缓存目录）；
- 网络保持共享（LLM 网关必须可达）。出口过滤（iptables/nft per-cgroup）需
  root，属二层未实装：dummy-key 设计下 agent 无真凭据，直连上游本就认证不过，
  网关侧另有"该来没来"对账缺口（roadmap A2）兜着。

口径纪律：默认 OFF（不动冻结口径），MEMHALL_SANDBOX=1 显式开启；开启状态
随 remote_environment 记入 manifest.environment.sandbox。Windows 本机车道
（subprocess argv 形态，无 shell 包装点）视时间另行处理，本模块只包 shell 命令串。
"""

from __future__ import annotations

import os
from shlex import quote

# 隔离 tmpfs 点：每条消息独立，跨 case 不残留（~ 由执行侧 shell 的 $HOME 展开）
_EPHEMERAL = ["/tmp", "$HOME/.cache", "$HOME/.npm"]

_TRUTHY = {"1", "true", "on", "bwrap"}


def sandbox_enabled() -> bool:
    """MEMHALL_SANDBOX ∈ {1,true,on,bwrap} 时启用；默认关（不动冻结口径）。"""
    return os.environ.get("MEMHALL_SANDBOX", "").strip().lower() in _TRUTHY


def bwrap_wrap(cmd: str, writable: list[str]) -> str:
    """把整条 send 脚本包进 bwrap：整根只读 + writable 白名单 + tmpfs 临时位。

    writable 用 ~ 相对形式（~/dev），由执行侧 shell 的 $HOME 展开；目标目录
    不存在时先 mkdir -p（EVAL_WORKDIRS 常在 agent 首写时才出现）。脚本内的
    mktemp/env 文件都落在沙箱内 /tmp（tmpfs），凭据 plumbing 一并隔离——
    沙箱外看不见这些临时文件。
    """
    dirs = [p.replace("~", "$HOME", 1) for p in writable]
    tmpfs = " ".join(f"--tmpfs {p}" for p in _EPHEMERAL)
    binds = " ".join(f"--bind {t} {t}" for t in dirs)
    return (f"mkdir -p {' '.join(dirs)} && "
            f"bwrap --ro-bind / / --dev /dev --proc /proc {tmpfs} {binds} "
            f"-- sh -c {quote(cmd)}")


def maybe_wrap(cmd: str, writable: list[str]) -> str:
    """send 脚本出口：沙箱开启时包 bwrap，否则原样返回（默认路径零改动）。"""
    return bwrap_wrap(cmd, writable) if sandbox_enabled() else cmd
