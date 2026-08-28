#!/usr/bin/env python3
"""DSH 启动守卫（dsh-guard）：闸②+闸③
包装「让插件生效的 DSH 重启」：
  启动成功判定 -> READY 文件出现且进程存活
  启动失败(进程提前退出/超时) -> 自动回滚备份 -> 重试一次
  仍失败 -> 进入安全模式(DSH_PLUGIN_SAFE=1 跳过全部插件) -> 必起
真实 DSH 用法示例（实现后）:
  python3 dsh_guard.py start --cmd "dsh web" --ready "..."
模拟演示（本期已验证）:
  python3 dsh_guard.py start --cmd 'python3 ~/project/plugins-framework/dsh_sim.py --fail-once'
  python3 dsh_guard.py start --cmd 'python3 ~/project/plugins-framework/dsh_sim.py --fail-always'
"""
import argparse
import os
import shutil
import signal
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(BASE, "plugins-repo")     # 正式插件目录（模拟）
BAK_DIR = os.path.join(BASE, "plugins-repo.bak")     # 备份
SIM_DIR = os.path.join(BASE, ".sim")
READY = os.path.join(SIM_DIR, "READY")

os.makedirs(PLUGINS_DIR, exist_ok=True)


def remember_bak():
    """每次准许替换前调用一次：把当前正式目录快照到 .bak（旧版保平安）。"""
    shutil.copytree(PLUGINS_DIR, BAK_DIR, dirs_exist_ok=True)
    print(f"[guard] 已备份当前正式插件目录 → {BAK_DIR}")


def rollback():
    if os.path.isdir(BAK_DIR):
        shutil.copytree(BAK_DIR, PLUGINS_DIR, dirs_exist_ok=True)
        print(f"[guard] 🔄 已回滚插件目录到 .bak 备份（旧版恢复）")
        return True
    print("[guard] 无备份可用，跳过回滚")
    return False


def wait_ready(proc, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:          # 进程提前退出 = 启动失败
            return "exited", proc.returncode
        if os.path.exists(READY):            # 就绪判定
            return "ready", 0
        time.sleep(0.5)
    return "timeout", None


def kill_proc(proc):
    """强杀 guard 拉起的进程组（start_new_session=True，pgid==pid）。
    timeout 场景进程可能还活着，必须回收，否则泄漏的坏 DSH 会占端口/资源。"""
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def launch(cmd, env_extra=None):
    if os.path.exists(READY):
        os.remove(READY)
    env = dict(os.environ, **(env_extra or {}))
    print(f"[guard] 启动: {cmd}" + (f"  [env {env_extra}]" if env_extra else ""))
    proc = subprocess.Popen(cmd, shell=True, env=env, start_new_session=True)
    return proc


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start")
    p.add_argument("--cmd", required=True, help="启动命令（真实场景为 dsh web）")
    p.add_argument("--timeout", type=int, default=20)
    p.set_defaults(fn=cmd_start)
    p = sub.add_parser("backup")            # 闸① 通过后、替换前调用
    p.set_defaults(fn=lambda a: remember_bak() or 0)
    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


def cmd_start(args):
    # attempt 1: 正常启动
    proc = launch(args.cmd)
    verdict, rc = wait_ready(proc, args.timeout)
    if verdict == "ready":
        print("[guard] ✅ 启动成功（插件正常生效）")
        print("  提示: 该进程独立于 guard；如需停止请 kill 其进程组，或 Ctrl+C 结束 guard 观察模式")
        try:
            while proc.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            print("[guard] 观察结束")
        return 0

    failed = f"启动失败({verdict}, rc={rc})"
    print(f"[guard] ⚠️ {failed} → 疑似坏插件导致，自动回滚重试…")
    kill_proc(proc)   # timeout 时旧进程可能还活着，先回收再重试
    # attempt 2: 回滚后重试
    rollback()
    proc2 = launch(args.cmd)
    verdict2, rc2 = wait_ready(proc2, args.timeout)
    if verdict2 == "ready":
        print("[guard] ✅ 回滚后启动成功（已恢复旧版插件，坏版本已被隔离）")
        print("  下一步: 用 supervisor.py approve <插件> 冒烟验证修复后再替换")
        try:
            while proc2.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            print("[guard] 观察结束")
        return 0

    # attempt 3: 安全模式（跳过全部插件）
    print(f"[guard] 🔥 回滚后仍失败({verdict2}, rc={rc2}) → 进入安全模式 DSH_PLUGIN_SAFE=1")
    kill_proc(proc2)
    proc3 = launch(args.cmd, env_extra={"DSH_PLUGIN_SAFE": "1"})
    verdict3, rc3 = wait_ready(proc3, args.timeout)
    if verdict3 == "ready":
        print("[guard] 🛡 安全模式启动成功：DSH 核心已就绪，全部用户插件被跳过")
        print("  请修复插件: 读加载日志 → 修 → supervisor.py approve 验证 → backup → 替换 → 重启")
        try:
            while proc3.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            print("[guard] 观察结束")
        return 0
    kill_proc(proc3)   # 安全模式也超时：进程回收，避免泄漏
    print(f"[guard] ❌ 安全模式也失败({verdict3}, rc={rc3}) —— 这已与插件无关，请检查 DSH 本体/环境")
    return 1


if __name__ == "__main__":
    main()
