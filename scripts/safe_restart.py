#!/usr/bin/env python3
"""safe_restart.py —— DSH 安全停止（保证「关闭前」会话安全；重启交给正常流程）

解决：手动杀进程 + 立即拉起 → 双进程写同一会话 → seq 重复 → 会话损坏（官方 #420）。
设计：只保证「关闭前安全」——存好档、等写完、防双写；重启后的保障由
启动链 guard（未来）/ 手动恢复兜底，本脚本不负责拉起。

流程：
  ① 备份会话      （调 backup.py sessions，reason=safe-restart → 会话日志）
  ② 优雅停止      （SIGTERM → 等 10s → 未退 SIGKILL；正则防自匹配）
  ③ 等端口释放    （循环探测直到 3080 无响应，确保旧进程写完会话）
  ④ 写还原标记    （供下次启动时 guard 自动检测/还原）
  ⑤ 提示重启      （不拉起，重启交给 App/正常流程）

用法：
  python3 safe_restart.py                  # 安全停止主 DSH
  python3 safe_restart.py --dry-run        # 只打印计划，不执行任何动作（测试用）
  python3 safe_restart.py --profile <名>   # 只操作指定 profile 的进程
  python3 safe_restart.py --no-backup      # 跳过备份（不建议）

⚠️ 本脚本会停止主 DSH 进程——仅在明确需要时执行；停止后请自行重启 DSH。
"""
import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request

DSH_HOME = os.environ.get("DSH_HOME", "/root/.dsh")
BASE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("DSH_WEB_PORT", "3080"))
GRACE = 10          # SIGTERM 后等待秒数
PORT_WAIT = 40      # 等端口释放最长秒数


def log(msg):
    print("[safe_restart] %s" % msg, flush=True)


def url():
    return "http://127.0.0.1:%d/" % PORT


def port_up(timeout=3):
    try:
        r = urllib.request.urlopen(url(), timeout=timeout)
        return True
    except Exception:
        return False


def find_web_pids(profile=None):
    """找 DSH web 进程，按 profile 过滤（防误杀其它实例/主进程）。

    - profile 为 None/默认：只匹配主 web（`bin.js web` 别名或 `--profile web`）
    - profile 指定（如 test-safe）：只匹配 `--profile <name>` 的进程
    用 /proc/<pid>/cmdline 精确匹配命令行，pgrep 正则仅做候选收集（防自匹配）。
    """
    out = subprocess.run(["pgrep", "-f", "bin[.]js"],
                         capture_output=True, text=True).stdout.strip()
    cands = [p for p in out.splitlines() if p.isdigit()]
    result = []
    for p in cands:
        try:
            with open("/proc/%s/cmdline" % p, "rb") as f:
                cmd = f.read().decode(errors="replace").replace("\0", " ")
        except Exception:
            continue
        if "bin.js" not in cmd:
            continue
        if profile:
            if "--profile %s " % profile in cmd + " " or (
                    profile == "web" and (" bin.js web " in cmd + " " or "--profile web" in cmd)):
                result.append(p)
        else:
            # 默认只匹配主 web：web 别名或 --profile web，且不是其它 --profile
            if ("bin.js web" in cmd and "--profile" not in cmd) or "--profile web" in cmd:
                result.append(p)
    return result


def stop_gracefully(pids):
    """SIGTERM → 等 GRACE 秒 → 未退 SIGKILL。返回最终存活的 pid 列表。"""
    for p in pids:
        try:
            os.kill(int(p), signal.SIGTERM)
        except Exception:
            pass
    deadline = time.time() + GRACE
    while time.time() < deadline:
        alive = [p for p in pids if _pid_alive(p)]
        if not alive:
            return []
        time.sleep(0.5)
    for p in pids:
        try:
            os.kill(int(p), signal.SIGKILL)
        except Exception:
            pass
    time.sleep(1)
    return [p for p in pids if _pid_alive(p)]


def _pid_alive(p):
    try:
        os.kill(int(p), 0)
        return True
    except Exception:
        return False


def wait_port_down(limit=PORT_WAIT):
    t0 = time.time()
    while time.time() - t0 < limit:
        if not port_up(2):
            return True
        time.sleep(1)
    return not port_up(2)


def run_backup():
    """调 backup.py sessions，返回备份名。"""
    r = subprocess.run([sys.executable, os.path.join(BASE, "backup.py"),
                        "sessions", "--reason", "safe-restart"],
                       capture_output=True, text=True)
    print(r.stdout.strip(), flush=True)
    for line in r.stdout.splitlines():
        if line.startswith("BACKUP_DIR="):
            return os.path.basename(line.split("=", 1)[1])
    return None


def write_pending(name):
    import json
    with open(os.path.join(DSH_HOME, ".restore-pending.json"), "w") as f:
        json.dump({"backup": name, "ts": time.strftime("%F %T"),
                   "reason": "safe-restart", "by": "safe_restart.py"}, f,
                  ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser(description="DSH 安全停止（保证关闭前会话安全；重启交给正常流程）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    ap.add_argument("--no-backup", action="store_true", help="跳过会话备份")
    ap.add_argument("--yes", action="store_true", help="跳过确认（脚本化场景）")
    ap.add_argument("--profile", default="", help="只操作指定 profile 的进程（默认仅主 web；测测试实例时传如 test-safe）")
    args = ap.parse_args()

    log("=== DSH 安全停止（profile=%s）===" % (args.profile or "主 web"))
    pids = find_web_pids(args.profile or None)
    log("目标进程: %s" % (", ".join(pids) if pids else "(未运行)"))

    if args.dry_run:
        log("--dry-run 计划（只保证关闭前安全，不拉起、不负责重启）：")
        log("  ① 备份: backup.py sessions（reason=safe-restart → 会话日志）")
        log("  ② 优雅停止: %s → SIGTERM → %ds → SIGKILL" % (pids or "无进程", GRACE))
        log("  ③ 等端口释放: %s ≤%ds（防 #420 双写）" % (url(), PORT_WAIT))
        log("  ④ 写还原标记（供启动链/手动 guard 自动还原）")
        log("  ⑤ 提示你重启 DSH（交给正常流程）")
        return 0

    if not args.yes and pids:
        r = input("确认停止 %s（进程 %s）？[y/N] "
                  % (args.profile or "主 DSH", ", ".join(pids)))
        if r.strip().lower() not in ("y", "yes"):
            log("已取消")
            return 1

    # ① 备份（关闭前存档）
    backup_name = None
    if not args.no_backup:
        backup_name = run_backup()
        if not backup_name:
            log("⚠️ 备份未生成，继续（无备份兜底）")
    else:
        log("跳过备份（--no-backup）")

    # ② 优雅停止（等正在写的东西写完）
    if pids:
        alive = stop_gracefully(pids)
        if alive:
            log("⚠️ 以下进程未能停止: %s（可能已退出）" % ", ".join(alive))

    # ③ 等端口释放（#420 解药：防双进程写会话）
    if not wait_port_down():
        log("⚠️ 端口 %d 仍占用——可能有残留进程，继续前先确认" % PORT)

    # ④ 写还原标记（下次启动时 guard 可自动检测/还原）
    if backup_name:
        try:
            write_pending(backup_name)
            log("已写还原标记: %s" % os.path.join(DSH_HOME, ".restore-pending.json"))
        except Exception as e:
            log("写标记失败: %s" % e)

    # ⑤ 不拉起——提示用户重启（安全关闭完成）
    log("✅ 安全停止完成。会话已备份: %s" % (backup_name or "(未备份)"))
    log("→ 请重启 DSH（App 重启 / 正常流程）。重启后会话损坏时，可运行:")
    log("   python3 /sdcard/Download/DSHA/工具/session_guard.py   # 自动检测+还原")
    log("   python3 backup.py restore %s --session <会话id>        # 手动单会话恢复" % backup_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
