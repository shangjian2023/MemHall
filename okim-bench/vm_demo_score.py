"""在 openKylin 虚拟机内对 KylinBot 真实 brain.db 跑评分全链路。

流程：
1. 拷贝真实 brain.db 到 /tmp（绝不改原库），注入 4 条带标签的测试记忆
2. 用适配器把 brain.db 归一为 MemorySnapshot
3. 用评分决策树对 4 个金标准 probe 评分（含 1 个故意错答案）
4. 输出评分结果 + 指标

在虚拟机内运行：python3 vm_demo_score.py
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scoring.models import ErrorType, EvidenceBundle, ProbeSpec
from scoring.tree import score_probe
from scoring.metrics import aggregate
from adapters.kylinbot import load_snapshot

REAL_BRAIN = Path.home() / ".kylinbot/workspace/memory/brain.db"
TEST_BRAIN = Path("/tmp/okim_bench_brain_test.db")

# 注入的测试记忆（明确标注为评测种子数据；key 有 UNIQUE 约束，版本链用不同 key）
SEED_ENTRIES = [
    ("user.daily_template", "core", "用户的日报模板在 /home/okim/templates/daily.docx", None),
    ("user.address", "core", "用户的地址是杭州市滨江区", None),
    ("user.address#v1", "core", "[历史版本] 用户的地址是杭州市西湖区", "user.address"),
    ("debug.temp_port", "session", "这次调试临时用端口8080", None),
]


def prepare_test_brain() -> None:
    # WAL 模式下未 checkpoint 的数据在 -wal 中，三个文件必须一起拷贝
    for suffix in ("", "-shm", "-wal"):
        src = Path(str(REAL_BRAIN) + suffix)
        if src.exists():
            shutil.copy(src, Path(str(TEST_BRAIN) + suffix))
    conn = sqlite3.connect(TEST_BRAIN)
    with conn:
        for key, category, content, superseded_by in SEED_ENTRIES:
            conn.execute(
                "INSERT OR REPLACE INTO memories "
                "(id, key, content, category, created_at, updated_at, importance, superseded_by) "
                "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'), 0.5, ?)",
                (f"seed-{key}-{content[:8]}", key, content, category, superseded_by),
            )
    conn.close()


def main() -> int:
    print("== 真实 brain.db 评分演示（openKylin VM 内） ==")
    prepare_test_brain()
    snap = load_snapshot(TEST_BRAIN)
    print(f"记忆库快照: {len(snap.entries)} 条")
    for e in snap.entries:
        print(f"  - [{e.source}] {e.text[:60]}")

    # 4 个 probe：3 个应通过、1 个故意错答案（临时端口被持久化）
    probes = [
        (ProbeSpec("VM-001", "memory_store_check", "retention",
                   expect_store_retain=["daily.docx"]), ErrorType.CORRECT),
        (ProbeSpec("VM-002", "memory_store_check", "update",
                   expect_store_retain=["杭州市滨江区"]), ErrorType.CORRECT),
        (ProbeSpec("VM-003", "memory_store_check", "boundary",
                   expect_store_absent=["端口8080"]), ErrorType.FALSE_PERSISTENCE),
        (ProbeSpec("VM-004", "memory_store_check", "retention",
                   expect_store_retain=["周报模板在 /srv/weekly"]), ErrorType.OMISSION),
    ]

    results, ok = [], True
    for spec, expect in probes:
        r = score_probe(spec, EvidenceBundle(spec.probe_id, spec.probe_id, memory_after=snap))
        d = r.to_dict()
        d["capability"] = spec.capability
        results.append(d)
        mark = "✓" if r.error_type == expect else "✗"
        ok = ok and r.error_type == expect
        print(f"{mark} {spec.probe_id} [{spec.capability}] 判定={r.error_type.value} "
              f"期望={expect.value} 证据={r.evidence_refs} 理由={r.reason[:50]}")

    metrics = aggregate(results)
    out = Path("/tmp/okim_vm_metrics.json")
    out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n指标: 六维={metrics['capability_scores']}")
    print(f"结果: {'全部通过' if ok else '存在不一致'}  指标文件: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
