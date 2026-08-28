#!/usr/bin/env python3
"""演示：思考链夹具 fixture + 实例测试「修改思考链」插件的完整链路。
插件 think_plugin.py 的逻辑：深度>=2 的思考追加 [reviewed] 标记；输出 diff 计划。
--dry-run: 只输出修改计划（金丝雀模式）；不带则为应用模式。"""
import json
import sys

# ---------- 夹具：真实思考链的格式缩影（schema 契约） ----------
FIXTURE = {
    "thoughts": [
        {"id": "t1", "role": "user", "depth": 0, "content": "用户在问部署方案"},
        {"id": "t2", "role": "internal", "depth": 1, "content": "先体检环境再设计"},
        {"id": "t3", "role": "internal", "depth": 2, "content": "seccomp 禁了 unshare"},
        {"id": "t4", "role": "internal", "depth": 2, "content": "进程级隔离依然可行"},
        {"id": "t5", "role": "summary", "depth": 0, "content": "给用户结论"},
    ]
}


def plan(input_data):
    """计算修改计划：返回 (前后对照清单)，不修改原数据。"""
    changes = []
    for t in input_data["thoughts"]:
        if t["depth"] >= 2 and "[reviewed]" not in t["content"]:
            changes.append(
                {"id": t["id"], "before": t["content"], "after": t["content"] + " [reviewed]"})
    return changes


def apply(input_data, changes):
    import copy
    data = copy.deepcopy(input_data)
    by_id = {c["id"]: c for c in changes}
    for t in data["thoughts"]:
        if t["id"] in by_id:
            t["content"] = by_id[t["id"]]["after"]
    return data


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    changes = plan(FIXTURE)
    print(f"[插件] 思考链共 {len(FIXTURE['thoughts'])} 条，计划修改 {len(changes)} 条", flush=True)
    for c in changes:
        print(f"  {c['id']}: {c['before'][:18]}... → {c['after'][:22]}...", flush=True)
    if not dry:
        out = apply(FIXTURE, changes)
        import os
        os.makedirs("/tmp/thinkfix", exist_ok=True)
        with open("/tmp/thinkfix/thoughts_out.json", "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("[插件] 已应用并写入 thoughts_out.json", flush=True)
    print("DONE", flush=True)
