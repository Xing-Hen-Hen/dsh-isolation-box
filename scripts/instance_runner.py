#!/usr/bin/env python3
"""DSH 多实例插件框架 · 实例侧骨架 (instance_runner)
职责：心跳上报 / 状态机 / 插件契约执行 / 结果落盘 / --smoke 冒烟模式
插件契约（由宿主写在 work/task.json → ctx 注入）：
    def init(ctx) -> None      # 可选：初始化（冒烟测试会执行到这里）
    def run(ctx) -> dict       # 可选：主逻辑，返回结果字典
ctx 提供:
    ctx.work_dir / ctx.task / ctx.report(status, detail) / ctx.log(msg)
"""
import argparse
import json
import os
import sys
import threading
import time
import traceback


def write_json(path, data):
    # 关键：tmp 文件名必须唯一（心跳线程与主线程并发写同一文件时防竞态）
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class Ctx:
    def __init__(self, work_dir, state_path, task):
        self.work_dir = work_dir
        self.task = task
        self._state_path = state_path

    def report(self, status, detail="", progress=None, **extra):
        st = read_json(self._state_path, {})
        st.update({"status": status, "detail": str(detail)[:500], "heartbeat": time.time()})
        if progress is not None:
            st["progress"] = progress
        st.update(extra)
        write_json(self._state_path, st)

    def log(self, msg):
        print(f"[plugin] {msg}", flush=True)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def load_plugin(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("dsh_plugin_" + os.path.basename(path), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--heartbeat-sec", type=float, default=2.0)
    ap.add_argument("--smoke", action="store_true", help="冒烟模式：仅加载+init，不执行 run")
    args = ap.parse_args()

    state_path = os.path.join(args.dir, "state.json")
    work_dir = os.path.join(args.dir, "work")
    os.makedirs(work_dir, exist_ok=True)
    write_json(state_path, {"status": "starting", "pid": os.getpid(), "heartbeat": time.time()})

    # ---- 心跳线程（daemon：主进程退出即停，最后状态由主线程落盘） ----
    stop = threading.Event()

    def heartbeat():
        while not stop.is_set():
            st = read_json(state_path, {})
            st.update({"status": st.get("status", "running"), "heartbeat": time.time()})
            write_json(state_path, st)
            stop.wait(args.heartbeat_sec)

    threading.Thread(target=heartbeat, daemon=True).start()

    # ---- 任务输入 ----
    task = read_json(os.path.join(work_dir, "task.json"), {})

    try:
        write_json(state_path, {"status": "running", "pid": os.getpid(), "heartbeat": time.time()})
        mod = load_plugin(args.plugin)
        ctx = Ctx(work_dir, state_path, task)

        if args.smoke:
            # 闸①：模拟 DSH 启动时的加载+初始化路径
            if hasattr(mod, "init"):
                mod.init(ctx)
            write_json(os.path.join(args.dir, "result.json"),
                       {"ok": True, "mode": "smoke", "finished_at": time.time()})
            print("[runner] 冒烟通过：加载+初始化成功", flush=True)
            stop.set()
            return 0

        if hasattr(mod, "init"):
            mod.init(ctx)
        ctx.report("running", detail="插件已初始化")
        if hasattr(mod, "run"):
            result = mod.run(ctx)
        else:
            result = {"ok": True, "msg": "插件无 run()"}
        write_json(os.path.join(args.dir, "result.json"),
                   {"ok": True, "result": result, "finished_at": time.time()})
        ctx.report("completed", detail="插件执行完成")
        print("[runner] 插件执行完成，结果已写入 result.json", flush=True)
        stop.set()
        return 0
    except Exception:
        tb = traceback.format_exc()
        print(tb, flush=True)
        write_json(state_path, {"status": "error", "heartbeat": time.time(),
                                "error": tb.strip().splitlines()[-1][:300], "pid": os.getpid()})
        stop.set()
        return 1


if __name__ == "__main__":
    sys.exit(main())
