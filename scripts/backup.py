#!/usr/bin/env python3
"""DSH 会话/配置备份工具（backup.py）—— 隔离箱「最后一步」保险的存档层。

职责：
  backup.py dsh           完整备份：会话 + 配置（settings.yaml + profile 清单）
  backup.py sessions      只备份会话（对话）—— 最后一次「最后一步」的推荐动作
  backup.py list          列出全部备份（名字/类型/时间/大小）
  backup.py restore <名>  还原：先备份当前状态，再用备份覆盖（会话还原后需重启 DSH 生效）
  backup.py verify <名>   校验备份完整性（文件数 + manifest）

设计要点：
  - 零第三方依赖（纯标准库），不依赖 DSH 运行 —— 会话损坏时仍可手动还原
  - 路径全部 $DSH_HOME 可覆盖（默认 /root/.dsh），测试可用假环境
  - sessions 是符号链接（→ /sdcard/...）时跟随复制实际内容（copytree symlinks=False）
  - 每类备份保留最近 KEEP=5 份，自动清理旧的
  - 每次备份写 manifest.json：类型/时间/来源/清单/还原指引
"""
import json
import os
import shutil
import sys
import time

DSH_HOME = os.environ.get("DSH_HOME", "/root/.dsh")
SESSIONS_SRC = os.path.join(DSH_HOME, "sessions")
BACKUP_ROOT = os.path.join(DSH_HOME, "backups")
CONFIG_FILES = ["settings.yaml"]
PROFILE_FILES = ["profiles/web/package.json", "cordis.patch.yml"]
KEEP = int(os.environ.get("DSH_BACKUP_KEEP", "5"))
CORRUPT_ROOT = os.environ.get("DSHA_CORRUPT_ROOT", os.path.join(DSH_HOME, "corrupt-backup"))


def now_ts():
    return time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (time.time_ns() % 1000)


def log(msg):
    line = "[%s] %s" % (time.strftime("%F %T"), msg)
    print(line, flush=True)
    try:
        with open(os.path.join(DSH_HOME, "backup.log"), "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def list_backups(kind=None):
    """返回 [{name, kind, ts, size, files}]，按时间倒序。"""
    out = []
    if not os.path.isdir(BACKUP_ROOT):
        return out
    for name in sorted(os.listdir(BACKUP_ROOT), reverse=True):
        d = os.path.join(BACKUP_ROOT, name)
        if not os.path.isdir(d):
            continue
        man = {}
        try:
            with open(os.path.join(d, "manifest.json")) as f:
                man = json.load(f)
        except Exception:
            pass
        k = man.get("kind", name.split("-", 1)[0] if "-" in name else "?")
        if kind and k != kind:
            continue
        size = sum(os.path.getsize(os.path.join(r, fn))
                   for r, _, fs in os.walk(d) for fn in fs)
        out.append({
            "name": name, "kind": k, "ts": man.get("ts", ""),
            "size": size, "files": man.get("files", 0),
            "reason": man.get("reason", ""),
        })
    return out


def keep_prune(kind):
    """同一类型只保留最近 KEEP 份。"""
    keep, removed = KEEP, 0
    for b in list_backups(kind):
        if keep > 0:
            keep -= 1
            continue
        try:
            shutil.rmtree(os.path.join(BACKUP_ROOT, b["name"]), ignore_errors=True)
            removed += 1
        except Exception:
            pass
    if removed:
        log("[backup] 已清理 %d 份旧备份（%s 类保留 %d 份）" % (removed, kind, KEEP))


def copy_sessions(dst):
    """复制会话目录（跟随软链复制实际内容）。返回文件数。"""
    if not os.path.isdir(SESSIONS_SRC):
        return 0
    shutil.copytree(SESSIONS_SRC, dst, symlinks=False)
    return sum(len(fs) for _, _, fs in os.walk(dst))


def copy_cfg(dst):
    """复制配置/插件清单（存在才拷）。返回文件数。"""
    n = 0
    for rel in CONFIG_FILES + PROFILE_FILES:
        src = os.path.join(DSH_HOME, rel)
        if os.path.isfile(src):
            d = os.path.join(dst, os.path.dirname(rel))
            os.makedirs(d, exist_ok=True)
            shutil.copy2(src, os.path.join(dst, rel))
            n += 1
    return n


def cmd_sessions(args):
    name = "sessions-" + now_ts()
    dst = os.path.join(BACKUP_ROOT, name)
    os.makedirs(dst)
    files = copy_sessions(os.path.join(dst, "sessions"))
    man = {"kind": "sessions", "name": name, "ts": now_ts(),
           "files": files, "reason": args.reason or "",
           "restore": "python3 backup.py restore %s" % name}
    with open(os.path.join(dst, "manifest.json"), "w") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    keep_prune("sessions")
    log("[backup] ✅ 会话已备份 → %s （%d 个文件）" % (dst, files))
    log("[backup] 还原方法: python3 backup.py restore %s" % name)
    print("BACKUP_DIR=%s" % dst)
    return 0


def cmd_dsh(args):
    name = "dsh-" + now_ts()
    dst = os.path.join(BACKUP_ROOT, name)
    os.makedirs(dst)
    files = copy_sessions(os.path.join(dst, "sessions"))
    files += copy_cfg(dst)
    man = {"kind": "dsh", "name": name, "ts": now_ts(), "files": files,
           "reason": args.reason or "",
           "restore": "python3 backup.py restore %s" % name}
    with open(os.path.join(dst, "manifest.json"), "w") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    keep_prune("dsh")
    log("[backup] ✅ DSH 备份完成 → %s （%d 个文件）" % (dst, files))
    print("BACKUP_DIR=%s" % dst)
    return 0


def cmd_list(args):
    rows = list_backups()
    if not rows:
        print("(无备份。backup.py sessions 创建第一份)")
        return 0
    print("%-22s %-9s %-17s %8s  %s" % ("名称", "类型", "时间", "大小", "说明"))
    for b in rows:
        print("%-22s %-9s %-17s %7.1fK  %s"
              % (b["name"], b["kind"], b["ts"], b["size"] / 1024.0, b.get("reason", "")))
    return 0


def cmd_verify(args):
    d = os.path.join(BACKUP_ROOT, args.name)
    if not os.path.isdir(d):
        print("❌ 备份不存在: %s" % args.name)
        return 1
    man = {}
    try:
        with open(os.path.join(d, "manifest.json")) as f:
            man = json.load(f)
    except Exception:
        pass
    files = sum(len(fs) for _, _, fs in os.walk(d))
    ok = not man or man.get("files", files) == files
    print("✅ 备份完整: %s （%d 个文件，manifest %s）" % (args.name, files, "一致" if ok else "不一致"))
    return 0 if ok else 1


def cmd_restore(args):
    d = os.path.join(BACKUP_ROOT, args.name)
    if not os.path.isdir(d):
        print("❌ 备份不存在: %s" % args.name)
        return 1
    man = {}
    try:
        with open(os.path.join(d, "manifest.json")) as f:
            man = json.load(f)
    except Exception:
        pass
    src_sessions = os.path.join(d, "sessions")
    if not os.path.isdir(src_sessions):
        print("❌ 备份里没有 sessions 目录（%s 不是会话备份）" % args.name)
        return 1

    # 还原前先备份当前状态（防误还原丢掉新对话）
    pre = os.path.join(CORRUPT_ROOT, "restore-pre-" + now_ts())
    if os.path.isdir(SESSIONS_SRC):
        os.makedirs(pre)
        shutil.copytree(SESSIONS_SRC, os.path.join(pre, "sessions"), symlinks=False)
        log("[restore] 还原前已备份当前会话 → %s" % pre)

    # 用备份覆盖当前 sessions
    if os.path.isdir(SESSIONS_SRC):
        shutil.rmtree(SESSIONS_SRC, ignore_errors=True)
    shutil.copytree(src_sessions, SESSIONS_SRC, symlinks=False)
    files = sum(len(fs) for _, _, fs in os.walk(SESSIONS_SRC))
    log("[restore] ✅ 已还原会话 ← %s （%d 个文件）" % (args.name, files))
    print("⚠️  还原后必须重启 DSH 才生效：python3 safe_restart.py --check 可先校验")
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="DSH 会话/配置备份工具（隔离箱保险）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sessions", help="只备份会话（对话）")
    p.add_argument("--reason", default="", help="备份说明")
    p.set_defaults(fn=cmd_sessions)
    p = sub.add_parser("dsh", help="完整备份（会话+配置+插件清单）")
    p.add_argument("--reason", default="", help="备份说明")
    p.set_defaults(fn=cmd_dsh)
    p = sub.add_parser("list", help="列出备份")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("verify")
    p.add_argument("name")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("restore")
    p.add_argument("name", help="备份名（backup.py list 查看）")
    p.set_defaults(fn=cmd_restore)
    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
