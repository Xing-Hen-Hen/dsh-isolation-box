#!/usr/bin/env python3
"""模拟任务插件：分 N 步执行真实任务（每步报进度），展示实例的「live 运行」。
任务参数可选: work/task.json {"steps": 6, "step_sec": 4, "label": "环境体检"}"""
import json
import os
import time


def init(ctx):
    ctx.log("任务插件初始化 OK")


def run(ctx):
    steps = int(ctx.task.get("steps", 6))
    step_sec = float(ctx.task.get("step_sec", 4))
    label = ctx.task.get("label", "模拟任务")
    results = []
    for i in range(1, steps + 1):
        ctx.report("running", detail=f"{label} 步骤 {i}/{steps}", progress=i)
        ctx.log(f"{label} 步骤 {i}/{steps} 执行中...")
        time.sleep(step_sec)
        results.append({"step": i, "ok": True})
    ctx.report("completed", detail=f"{label} 全部完成")
    return {"ok": True, "msg": f"{label}完成，共 {steps} 步", "steps": results}
