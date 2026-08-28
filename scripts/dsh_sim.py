#!/usr/bin/env python3
"""模拟 DSH 启动器（用于演示 dsh-guard 回滚/安全模式；不碰真实 DSH）。
--fail-once : 首次启动即失败（模拟刚替换的坏插件让 DSH 启动失败），回滚后第二次成功
--fail-always : 永远失败（模拟回滚也救不回来 → 触发安全模式）
成功标准：写 READY 文件并保持运行直到 SIGTERM。"""
import os
import signal
import sys
import time

# 与 dsh_guard.py / demo_story.py 的 SIM 约定保持一致：基于自身文件位置，
# 而不是写死 /root/project/...（解压到任何目录都能跑，README 的演示才成立）
SIM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sim")
READY = os.path.join(SIM_DIR, "READY")
COUNTER = os.path.join(SIM_DIR, "start_count.txt")


def main():
    os.makedirs(SIM_DIR, exist_ok=True)
    exit_after = None
    if "--exit-after" in sys.argv:
        try:
            exit_after = float(sys.argv[sys.argv.index("--exit-after") + 1])
        except (ValueError, IndexError):
            exit_after = None
    n = 0
    if os.path.exists(COUNTER):
        n = int(open(COUNTER).read().strip() or "0")
    n += 1
    open(COUNTER, "w").write(str(n))

    safe = os.environ.get("DSH_PLUGIN_SAFE") == "1"
    fail_once = "--fail-once" in sys.argv and n == 1 and not safe
    fail_always = "--fail-always" in sys.argv and not safe

    if fail_once or fail_always:
        reason = "首次启动失败(模拟坏插件)" if fail_once else "回滚后仍失败(模拟插件持续坏)"
        print(f"[sim-DSH] 启动失败: {reason} (第 {n} 次尝试)", flush=True)
        time.sleep(1)
        return 2

    if safe:
        print(f"[sim-DSH] 以安全模式启动(DSH_PLUGIN_SAFE=1, 跳过全部插件), 第 {n} 次尝试", flush=True)
    else:
        print(f"[sim-DSH] 启动成功, 第 {n} 次尝试, READY 就绪", flush=True)
    open(READY, "w").write(str(time.time()))

    def bye(*_):
        print("[sim-DSH] 收到退出信号，退出", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    if exit_after:
        time.sleep(exit_after)
        print("[sim-DSH] 演示用时已到(--exit-after)，自动退出", flush=True)
        return 0
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
