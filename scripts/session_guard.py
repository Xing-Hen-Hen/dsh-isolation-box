#!/usr/bin/env python3
"""session_guard.py —— 会话还原守卫（独立于 DSH/Agent 运行）

为什么需要它：会话损坏时，会话里的 Agent 已不存在，还原动作必须由
「DSH 启动前的外部守卫」执行。本脚本零依赖（纯标准库）、不 import 任何
DSH 包，可被 safe_restart / watchdog / 启动链在 DSH 拉起前调用。

工作模式：
  1. 有还原标记 $DSH_HOME/.restore-pending.json（safe_restart 重启前写入）：
     - 校验标记指定的备份存在
     - 逐文件对比「当前会话」vs「备份会话」（hash）
     - 不一致 → 自动还原（损坏文件先挪进 corrupt-backup）→ 删标记 → 报告
     - 一致   → 删标记 → 报告「会话健康，无需还原」
  2. 无标记（--check）：只做健康提示，不还原。

调用方：
  safe_restart.py 在「拉起 DSH 前」调用本脚本（最可靠）
  watchdog / 启动链可在拉起前调用（标记存在时才会动作，幂等安全）
"""
import hashlib
import json
import os
import shutil
import sys
import time

DSH_HOME = os.environ.get("DSH_HOME", "/root/.dsh")
SESSIONS = os.path.join(DSH_HOME, "sessions")
# 与 backup.py 保持一致：外层「当前会话编号」文件夹下分 auto-backups/session-logs
DSHA_BASE = os.environ.get("DSH_BACKUP_ROOT", "/sdcard/Download/DSHA")
_SID = os.environ.get("DSH_SESSION_ID", "").strip() or "session-unknown"
BACKUP_BASE = os.path.join(DSHA_BASE, _SID)
AUTO_SUBDIR = "auto-backups"
LOG_SUBDIR = "session-logs"
PENDING = os.path.join(DSH_HOME, ".restore-pending.json")
CORRUPT_ROOT = os.environ.get("DSHA_CORRUPT_ROOT", os.path.join(DSH_HOME, "corrupt-backup"))


def find_backup_sessions(name):
    """在两个备份子目录找 <name>/sessions，返回完整路径或 None。"""
    for base in (os.path.join(BACKUP_BASE, LOG_SUBDIR),
                 os.path.join(BACKUP_BASE, AUTO_SUBDIR)):
        p = os.path.join(base, name, "sessions")
        if os.path.isdir(p):
            return p
    return None


def log(msg):
    line = "[%s] %s" % (time.strftime("%F %T"), msg)
    print(line, flush=True)
    try:
        with open(os.path.join(DSH_HOME, "guard.log"), "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_pending():
    try:
        with open(PENDING) as f:
            return json.load(f)
    except Exception:
        return None


def clear_pending():
    try:
        os.remove(PENDING)
    except Exception:
        pass


def file_sha(p):
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for ch in iter(lambda: f.read(1 << 20), b""):
                h.update(ch)
        return h.hexdigest()
    except Exception:
        return None


def tree_files(root):
    """{相对路径: sha}，跟随软链读取内容。"""
    out = {}
    if not os.path.isdir(root):
        return out
    for r, _, fs in os.walk(root):
        for fn in fs:
            p = os.path.join(r, fn)
            rel = os.path.relpath(p, root)
            out[rel] = file_sha(p)
    return out


def sessions_differ(backup_sessions):
    """当前会话 vs 备份：文件集合或内容任一不同即认为需要还原。"""
    cur = tree_files(SESSIONS)
    bak = tree_files(backup_sessions)
    if set(cur) != set(bak):
        return True
    for rel, sha in bak.items():
        if cur.get(rel) != sha:
            return True
    return False


def do_restore(name, backup_sessions):
    # 还原前把当前（可疑损坏的）会话挪进 corrupt-backup 留证
    ts = time.strftime("%Y%m%d-%H%M%S")
    if os.path.isdir(SESSIONS):
        dst = os.path.join(CORRUPT_ROOT, "guard-pre-" + ts)
        os.makedirs(dst)
        try:
            shutil.copytree(SESSIONS, os.path.join(dst, "sessions"), symlinks=False)
            log("[guard] 可疑会话已留证 → %s" % dst)
        except Exception as e:
            log("[guard] 留证失败(继续还原): %s" % e)
        shutil.rmtree(SESSIONS, ignore_errors=True)
    shutil.copytree(backup_sessions, SESSIONS, symlinks=False)
    n = sum(len(fs) for _, _, fs in os.walk(SESSIONS))
    log("[guard] ✅ 已自动还原会话 ← 备份 %s （%d 个文件）" % (name, n))
    return True


def main():
    check_only = "--check" in sys.argv
    print("[guard] 操作目标: %s （确认 DSH_HOME 指向正确！）" % DSH_HOME, flush=True)
    pend = read_pending()
    if not pend:
        if check_only:
            log("[guard] 无还原标记，会话由 DSH 正常加载（如需强制校验请先备份）")
        else:
            log("[guard] 无还原标记，无事可做（幂等）")
        return 0

    name = pend.get("backup", "")
    backup_sessions = find_backup_sessions(name)
    if not backup_sessions:
        log("[guard] ⚠️ 标记指向的备份不存在: %s —— 跳过还原，保留标记供人工处理" % name)
        print("GUARD_SKIP_BACKUP_MISSING")
        return 2

    if not sessions_differ(backup_sessions):
        clear_pending()
        log("[guard] 会话与备份一致（重启未损坏），还原标记已清除")
        print("GUARD_OK_NO_CHANGE")
        return 0

    # 会话与备份不一致 → 还原（除非 --check 只报告）
    if check_only:
        log("[guard] ⚠️ 会话与备份不一致（可能损坏），将自动还原 ← %s" % name)
        print("GUARD_NEED_RESTORE")
        return 3
    do_restore(name, backup_sessions)
    clear_pending()
    log("[guard] 还原标记已清除，DSH 将加载还原后的会话")
    print("GUARD_RESTORED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
