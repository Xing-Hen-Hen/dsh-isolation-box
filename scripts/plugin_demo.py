#!/usr/bin/env python3
"""演示插件：前 2 次运行崩溃（模拟调试期 bug），第 3 次起成功 —— 展示自愈。
DEMO_MODE=crash_all 时永远崩溃（展示熔断）。契约: init(ctx) / run(ctx) -> dict"""
import os
import time


def _count_file(ctx):
    return os.path.join(ctx.work_dir, "run_count.txt")


def init(ctx):
    ctx.log(f"插件初始化 OK（进程 pid={os.getpid()}）")


def run(ctx):
    n = 0
    if os.path.exists(_count_file(ctx)):
        n = int(open(_count_file(ctx)).read().strip() or "0")
    n += 1
    open(_count_file(ctx), "w").write(str(n))
    ctx.report("running", detail=f"第 {n} 次执行", progress=n)

    if os.environ.get("DEMO_MODE") == "crash_all":
        raise RuntimeError(f"演示崩溃：DEMO_MODE=crash_all 第 {n} 次执行必崩")

    if n < 3:  # 模拟「前 2 版有 bug」
        raise RuntimeError(f"演示崩溃：这是第 {n} 次执行，模拟 v{n} 版本有 bug")

    time.sleep(1)
    ctx.report("running", detail="计算完成")
    return {"ok": True, "msg": "自愈成功", "tries": n, "pid": os.getpid()}
