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
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

DSH_HOME = os.environ.get("DSH_HOME", "/root/.dsh")
SESSIONS_SRC = os.path.join(DSH_HOME, "sessions")
# 备份分两类存放，外层套「当前会话编号」文件夹，都默认在 DSHA 下载目录下：
#   /sdcard/Download/DSHA/<会话编号>/
#     ├── auto-backups/      ← 自动备份（隔离箱触发，完整备份）
#     ├── session-logs/      ← 日志备份（显式/finalize/safe_restart）
#     ├── tools/             ← 恢复工具独立副本（backup.py + session_guard.py）
#     └── 恢复工具使用手册.md  ← 跟随自动备份生成
# DSH_BACKUP_ROOT 环境变量可覆盖外层父目录
DSHA_BASE = os.environ.get("DSH_BACKUP_ROOT", "/sdcard/Download/DSHA")


def current_session_id():
    """当前会话编号（dsh 注入的 DSH_SESSION_ID；缺失时取最新会话目录）。"""
    sid = os.environ.get("DSH_SESSION_ID", "").strip()
    if sid:
        return sid
    try:
        import glob as _g
        cands = _g.glob(os.path.join(SESSIONS_SRC, "--root-project--", "session-*"))
        if cands:
            return os.path.basename(sorted(cands, key=os.path.getmtime)[-1])
    except Exception:
        pass
    return "session-unknown"


BACKUP_BASE = os.path.join(DSHA_BASE, current_session_id())
AUTO_SUBDIR = "auto-backups"
LOG_SUBDIR = "session-logs"
TOOLS_SUBDIR = "tools"
MANUAL_NAME = "恢复工具使用手册.md"
CONFIG_FILES = ["settings.yaml"]
PROFILE_FILES = ["profiles/web/package.json", "cordis.patch.yml"]
KEEP = int(os.environ.get("DSH_BACKUP_KEEP", "5"))
CORRUPT_ROOT = os.environ.get("DSHA_CORRUPT_ROOT", os.path.join(DSH_HOME, "corrupt-backup"))


def backup_root(auto=False):
    """备份根目录：auto=True → 自动备份；否则 → 会话日志。"""
    return os.path.join(BACKUP_BASE, AUTO_SUBDIR if auto else LOG_SUBDIR)


def backup_roots():
    """两个备份子目录（list/restore/verify 都要找）。"""
    return [backup_root(False), backup_root(True)]


def ensure_tools():
    """把恢复工具（backup.py + session_guard.py）复制一份到备份父目录的「tools」子文件夹。

    目的：程序/插件损坏导致插件内脚本不可用时，仍可从 sdcard 直接运行恢复工具
    （钥匙与数据永远在一起）。幂等：已存在则覆盖更新；运行时自身（工具副本）跳过。
    成功静默（写 backup.log），失败打印警告——不污染 list 等命令的输出。
    """
    tools_dir = os.path.join(BACKUP_BASE, TOOLS_SUBDIR)
    try:
        os.makedirs(tools_dir, exist_ok=True)
        here = os.path.dirname(os.path.abspath(__file__))
        copied = []
        for fn in ("backup.py", "session_guard.py"):
            src = os.path.join(here, fn)
            dst = os.path.join(tools_dir, fn)
            if not os.path.isfile(src):
                continue
            try:
                same = os.path.exists(dst) and os.path.samefile(src, dst)
            except Exception:
                same = False
            if not same:
                shutil.copy2(src, dst)
                copied.append(fn)
        if copied:
            with open(os.path.join(DSH_HOME, "backup.log"), "a") as f:
                f.write("[%s] [backup] 恢复工具已同步 → %s （%s）\n"
                        % (time.strftime("%F %T"), tools_dir, ", ".join(copied)))
    except Exception as e:
        print("[backup] ⚠️ 恢复工具同步失败（不影响备份）: %s" % e)


def file_md5(p):
    """文件 md5 指纹（流式计算，不占内存）。"""
    h = hashlib.md5()
    try:
        with open(p, "rb") as f:
            for ch in iter(lambda: f.read(1 << 20), b""):
                h.update(ch)
        return h.hexdigest()
    except Exception:
        return None


def verify_restore(src, dst, scope="还原"):
    """还原后自动校验：src 与 dst 逐字节一致（md5 指纹对比）。

    返回 (ok, 明细)：ok=True 表示位置与内容完全正确。
    """
    try:
        if os.path.isdir(src) and os.path.isdir(dst):
            src_map = {}
            for r, _, fs in os.walk(src):
                for fn in fs:
                    rel = os.path.relpath(os.path.join(r, fn), src)
                    src_map[rel] = file_md5(os.path.join(r, fn))
            bad = []
            for rel, md in src_map.items():
                dp = os.path.join(dst, rel)
                if not os.path.isfile(dp):
                    bad.append("%s(缺失)" % rel)
                elif file_md5(dp) != md:
                    bad.append("%s(内容不符)" % rel)
            if bad:
                return False, "%s: %d/%d 个文件不一致: %s" % (scope, len(bad), len(src_map), "; ".join(bad[:5]))
            return True, "%s: %d 个文件全部一致（md5 指纹相同）" % (scope, len(src_map))
        # 单文件
        sm = file_md5(src)
        dm = file_md5(dst)
        if sm and sm == dm:
            return True, "%s: md5 一致（%s）" % (scope, sm[:12])
        return False, "%s: md5 不一致（源 %s vs 目标 %s）" % (scope, sm, dm)
    except Exception as e:
        return False, "校验异常: %s" % e


def now_ts():
    return time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (time.time_ns() % 1000)


def write_folder_readme(root, sub):
    """在每个备份子文件夹里生成/更新 README.txt：说明文件夹是什么、备份清单与时间。

    每次备份后调用，让用户在手机文件管理器里一眼看懂这个文件夹里有什么。
    """
    try:
        os.makedirs(root, exist_ok=True)
        desc = {
            "auto-backups": "隔离箱启动/触发时自动备份（完整备份：会话+配置+插件清单），用于恢复数据",
            "session-logs": "日志备份（backup.py 显式 / finalize / safe_restart），只含会话（对话）",
        }.get(sub, "DSH 会话备份")
        rows = list_backups(root=root)
        lines = [
            "本文件夹：%s/" % sub,
            "内容：%s" % desc,
            "保存位置：%s" % root,
            "还原方法：python3 backup.py restore <备份名> --session <会话id>",
            "",
            "备份清单（新→旧）：",
        ]
        if not rows:
            lines.append("  （暂无备份）")
        for b in rows:
            ts = b["ts"][:15] if len(b["ts"]) >= 15 else b["ts"]
            lines.append("  - %s（%s，%s 个文件，%s）"
                         % (b["name"], ts, b["files"], b.get("reason", "无说明") or "无说明"))
        lines.append("")
        lines.append("最近备份时间：%s" % (rows[0]["ts"] if rows else "（暂无）"))
        lines.append("本说明生成/更新时间：%s" % time.strftime("%F %T"))
        with open(os.path.join(root, "README.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        log("[backup] ⚠️ README 生成失败: %s" % e)


MANUAL_TEXT = """# 恢复工具使用手册（极简版）

> 工具：`/sdcard/Download/DSHA/%s/tools/backup.py`
> 备份：`auto-backups/`（完整）、`session-logs/`（会话）

## 1. 看有哪些备份

```bash
python3 %s/tools/backup.py list
```

## 2. 会话坏了，救回来（最常用）

```bash
# 先停 DSH（运行中不能还原，这是保护）
python3 %s/tools/backup.py restore <备份名> --session <会话id>
# 重启 DSH 就好了
```

## 3. 全部数据丢了，整体恢复

```bash
python3 %s/tools/backup.py restore <备份名> --yes
```

## 4. 检查备份有没有坏

```bash
python3 %s/tools/backup.py verify <备份名>
```

---

### 记住三条

1. **先停 DSH 再还原**
2. **还原后要重启 DSH** 才生效
3. **还原会把数据回到备份那一刻**（之后新增的对话会没，但还原前会自动留一份）

### 不会用？直接对我说

"用最近的备份恢复会话 xxx" —— 我来帮你跑。
"""


def write_manual():
    """把「恢复工具使用手册」写到外层文件夹（跟随自动备份生成/更新）。"""
    try:
        os.makedirs(BACKUP_BASE, exist_ok=True)
        with open(os.path.join(BACKUP_BASE, MANUAL_NAME), "w") as f:
            f.write(MANUAL_TEXT % (os.path.basename(BACKUP_BASE), BACKUP_BASE,
                                   BACKUP_BASE, BACKUP_BASE, BACKUP_BASE))
    except Exception as e:
        log("[backup] ⚠️ 使用手册生成失败: %s" % e)


def log(msg):
    line = "[%s] %s" % (time.strftime("%F %T"), msg)
    print(line, flush=True)
    try:
        with open(os.path.join(DSH_HOME, "backup.log"), "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def list_backups(kind=None, root=None):
    """返回 [{name, kind, ts, size, files, sub}]，按时间倒序。root 缺省遍历两个子目录。"""
    out = []
    roots = [root] if root else backup_roots()
    for base in roots:
        if not os.path.isdir(base):
            continue
        sub = os.path.basename(base)
        for name in sorted(os.listdir(base), reverse=True):
            d = os.path.join(base, name)
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
                "reason": man.get("reason", ""), "sub": sub,
            })
    out.sort(key=lambda b: b["ts"], reverse=True)
    return out


def find_backup(name):
    """在两个子目录里找备份，返回 (目录, 完整路径) 或 (None, None)。"""
    for base in backup_roots():
        d = os.path.join(base, name)
        if os.path.isdir(d):
            return base, d
    return None, None


def keep_prune(kind, root):
    """同一子目录内同一类型只保留最近 KEEP 份。"""
    keep, removed = KEEP, 0
    for b in list_backups(kind, root):
        if keep > 0:
            keep -= 1
            continue
        try:
            shutil.rmtree(os.path.join(root, b["name"]), ignore_errors=True)
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


def cmd_full(args):
    """程序级完整备份：打包整个 $DSH_HOME（.dsh，含插件/node_modules）成 tar.gz，
    内容与 DSHA「工作区备份」一致——程序损坏时用自动备份（最新）恢复程序。

    排除 backups/corrupt-backup 防止递归膨胀；sessions 软链按链接保留（与 DSHA 一致）。
    """
    name = "dsh-full-" + now_ts()
    root = backup_root(True)
    os.makedirs(root, exist_ok=True)
    dst_dir = os.path.join(root, name)          # 目录结构与 sessions/dsh 一致
    os.makedirs(dst_dir)
    tarball = os.path.join(dst_dir, name + ".tar.gz")
    parent = os.path.dirname(os.path.abspath(DSH_HOME))
    base = os.path.basename(os.path.abspath(DSH_HOME))
    excl = ["--exclude=%s" % os.path.join(base, "backups"),
            "--exclude=%s" % os.path.join(base, "corrupt-backup")]
    r = subprocess.run(["tar", "-czf", tarball] + excl +
                       ["-C", parent, base], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(tarball):
        print("❌ 程序级备份失败: %s" % (r.stderr or "")[-200:])
        return 1
    size = os.path.getsize(tarball) / 1024.0 / 1024.0
    man = {"kind": "dsh-full", "name": name, "ts": now_ts(),
           "reason": args.reason or "", "sub": AUTO_SUBDIR,
           "size_mb": round(size, 1), "restore": "解压 tar.gz 到 %s 覆盖" % DSH_HOME}
    with open(os.path.join(dst_dir, "manifest.json"), "w") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    keep_prune("dsh-full", root)
    write_folder_readme(root, AUTO_SUBDIR)
    if getattr(args, "auto", False):
        write_manual()
    log("[backup] ✅ 程序级完整备份 → %s （%.1f MB）" % (tarball, size))
    print("BACKUP_DIR=%s" % tarball)
    return 0


def cmd_sessions(args):
    name = "sessions-" + now_ts()
    root = backup_root(getattr(args, "auto", False))
    sub = AUTO_SUBDIR if getattr(args, "auto", False) else LOG_SUBDIR
    dst = os.path.join(root, name)
    os.makedirs(dst)
    files = copy_sessions(os.path.join(dst, "sessions"))
    man = {"kind": "sessions", "name": name, "ts": now_ts(),
           "files": files, "reason": args.reason or "", "sub": sub,
           "restore": "python3 backup.py restore %s" % name}
    with open(os.path.join(dst, "manifest.json"), "w") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    keep_prune("sessions", root)
    write_folder_readme(root, sub)
    if getattr(args, "auto", False):
        write_manual()
    log("[backup] ✅ 会话已备份 → %s （%d 个文件）" % (dst, files))
    log("[backup] 还原方法: python3 backup.py restore %s" % name)
    print("BACKUP_DIR=%s" % dst)
    return 0


def cmd_dsh(args):
    name = "dsh-" + now_ts()
    root = backup_root(getattr(args, "auto", False))
    sub = AUTO_SUBDIR if getattr(args, "auto", False) else LOG_SUBDIR
    dst = os.path.join(root, name)
    os.makedirs(dst)
    files = copy_sessions(os.path.join(dst, "sessions"))
    files += copy_cfg(dst)
    man = {"kind": "dsh", "name": name, "ts": now_ts(), "files": files,
           "reason": args.reason or "", "sub": sub,
           "restore": "python3 backup.py restore %s" % name}
    with open(os.path.join(dst, "manifest.json"), "w") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    keep_prune("dsh", root)
    write_folder_readme(root, sub)
    if getattr(args, "auto", False):
        write_manual()
    log("[backup] ✅ DSH 备份完成 → %s （%d 个文件）" % (dst, files))
    print("BACKUP_DIR=%s" % dst)
    return 0


def cmd_list(args):
    rows = list_backups()
    if not rows:
        print("(无备份。backup.py sessions 创建第一份)")
        return 0
    print("%-9s %-22s %-9s %-17s %8s  %s" % ("位置", "名称", "类型", "时间", "大小", "说明"))
    for b in rows:
        print("%-9s %-22s %-9s %-17s %7.1fK  %s"
              % (b.get("sub", "?"), b["name"], b["kind"], b["ts"],
                 b["size"] / 1024.0, b.get("reason", "")))
    return 0


def cmd_verify(args):
    base, d = find_backup(args.name)
    if not d:
        print("❌ 备份不存在: %s（自动备份/会话日志 两处均已查找）" % args.name)
        return 1
    man = {}
    try:
        with open(os.path.join(d, "manifest.json")) as f:
            man = json.load(f)
    except Exception:
        pass
    # 统计文件数时排除 manifest.json（它不属于备份内容，只算会话/配置文件）
    files = sum(1 for r, _, fs in os.walk(d) for fn in fs if fn != "manifest.json")
    ok = (not man) or man.get("files", files) == files
    if ok:
        print("✅ 备份完整: %s （%d 个文件，manifest 一致）" % (args.name, files))
        return 0
    print("❌ 备份不完整: %s （实有 %d 个文件，manifest 记录 %s 个）"
          % (args.name, files, man.get("files", "?")))
    return 1


def dsh_running():
    """检测 dsh 主进程是否在运行（防运行中还原造成索引错乱）。"""
    try:
        out = subprocess.run(["pgrep", "-f", "bin[.]js"],
                             capture_output=True, text=True).stdout
        return [p for p in out.splitlines() if p.isdigit()]
    except Exception:
        return []


def find_session_in_backup(src_sessions, session_id):
    """在备份树里按会话 id 找 session 文件。
    返回 (备份文件路径, 相对目录路径) 或 (None, None)。"""
    sid = session_id.strip()
    for r, _, fs in os.walk(src_sessions):
        for fn in fs:
            if not fn.startswith("session.jsonl"):
                continue
            p = os.path.join(r, fn)
            parts = p.replace("\\", "/").split("/")
            if sid in parts:
                rel = os.path.relpath(os.path.dirname(p), src_sessions)
                return p, rel
    return None, None


def cmd_restore(args):
    base, d = find_backup(args.name)
    print("⚠️  还原目标目录: %s/sessions （确认 DSH_HOME 指向正确！）" % DSH_HOME)
    if not d:
        print("❌ 备份不存在: %s（自动备份/会话日志 两处均已查找）" % args.name)
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

    # 还原目标 = 软链解析后的真实位置（sessions 通常是软链 → /sdcard/...；
    # 还原到真实位置、保持链接不动，否则 DSH 会找不到会话）
    real_sessions = os.path.realpath(SESSIONS_SRC)
    print("⚠️  还原到真实位置: %s" % real_sessions)

    # 安全闸 ①：运行中还原拒绝（上次事故教训：运行中还原 → 会话索引错乱）
    if not args.force:
        running = dsh_running()
        if running:
            print("❌ 检测到 dsh 正在运行（pid=%s）——运行中还原会造成会话错乱！" % ", ".join(running))
            print("   请先停止 dsh（safe_restart.py 或手动）再还原；确要强制请加 --force")
            return 1

    # ---- 单会话恢复模式（默认推荐） ----
    if getattr(args, "session", None):
        bak_file, rel = find_session_in_backup(src_sessions, args.session)
        if not bak_file:
            print("❌ 备份里找不到会话 %s（backup.py list 查看备份；会话 id 形如 session-xxxx）" % args.session)
            return 1
        dst_dir = os.path.join(real_sessions, rel)
        os.makedirs(dst_dir, exist_ok=True)
        dst_file = os.path.join(dst_dir, os.path.basename(bak_file))
        # 单会话还原前，备份当前该会话（防误还原丢新对话）
        if os.path.isfile(dst_file):
            pre = os.path.join(CORRUPT_ROOT, "restore-pre-" + now_ts())
            os.makedirs(pre, exist_ok=True)
            shutil.copy2(dst_file, os.path.join(pre, os.path.basename(dst_file)))
            log("[restore] 单会话还原前已备份当前版本 → %s" % pre)
        shutil.copy2(bak_file, dst_file)
        log("[restore] ✅ 已还原单个会话 %s ← %s （%s）" % (args.session, args.name, os.path.basename(bak_file)))
        # 自动校验：还原出的文件 vs 备份文件（md5 指纹）
        ok, detail = verify_restore(bak_file, dst_file, "单会话还原")
        print("[restore] %s" % ("✅ 校验通过：" + detail if ok else "❌ 校验失败：" + detail))
        print("⚠️  还原后请重启 DSH 才生效：python3 safe_restart.py --check 可先校验")
        return 0 if ok else 2

    # ---- 整树还原模式（影响所有会话，必须确认） ----
    print("⚠️  ⚠️  整树还原会把【所有会话】回退到备份点（%s）——上次事故教训！" % args.name)
    if not args.yes:
        r = input("确认整树还原全部会话？[y/N] ").strip().lower()
        if r not in ("y", "yes"):
            print("已取消（建议改用: backup.py restore %s --session <会话id> 单会话恢复）" % args.name)
            return 1

    # 还原前先备份当前状态（防误还原丢掉新对话）
    pre = os.path.join(CORRUPT_ROOT, "restore-pre-" + now_ts())
    if os.path.isdir(real_sessions):
        os.makedirs(pre)
        shutil.copytree(real_sessions, os.path.join(pre, "sessions"), symlinks=False)
        log("[restore] 还原前已备份当前会话 → %s" % pre)

    # 用备份覆盖真实位置（删除旧内容；链接本身不动）
    if os.path.isdir(real_sessions):
        shutil.rmtree(real_sessions, ignore_errors=True)
    shutil.copytree(src_sessions, real_sessions, symlinks=False)
    files = sum(len(fs) for _, _, fs in os.walk(real_sessions))
    log("[restore] ✅ 已整树还原会话 ← %s （%d 个文件）" % (args.name, files))
    # 自动校验：还原出的整树 vs 备份整树（逐文件 md5）
    ok, detail = verify_restore(src_sessions, real_sessions, "整树还原")
    print("[restore] %s" % ("✅ 校验通过：" + detail if ok else "❌ 校验失败：" + detail))
    print("⚠️  还原后必须重启 DSH 才生效：python3 safe_restart.py --check 可先校验")
    return 0 if ok else 2


def main():
    import argparse
    ap = argparse.ArgumentParser(description="DSH 会话/配置备份工具（隔离箱保险）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sessions", help="只备份会话（对话）→ 会话日志 子目录")
    p.add_argument("--reason", default="", help="备份说明")
    p.add_argument("--auto", action="store_true", help="隔离箱启动自动备份 → 自动备份 子目录")
    p.set_defaults(fn=cmd_sessions)
    p = sub.add_parser("dsh", help="数据备份（会话+配置+插件清单）→ 会话日志 子目录")
    p.add_argument("--reason", default="", help="备份说明")
    p.add_argument("--auto", action="store_true", help="隔离箱启动自动备份 → 自动备份 子目录")
    p.set_defaults(fn=cmd_dsh)
    p = sub.add_parser("full", help="程序级完整备份（整个 .dsh 打包，同 DSHA 备份）→ auto-backups 子目录")
    p.add_argument("--reason", default="", help="备份说明")
    p.add_argument("--auto", action="store_true", help="隔离箱启动自动备份")
    p.set_defaults(fn=cmd_full)
    p = sub.add_parser("list", help="列出备份")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("verify")
    p.add_argument("name")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("restore")
    p.add_argument("name", help="备份名（backup.py list 查看）")
    p.add_argument("--session", default="", help="只恢复指定会话（默认整树还原，强烈建议指定）")
    p.add_argument("--yes", action="store_true", help="跳过整树还原确认")
    p.add_argument("--force", action="store_true", help="dsh 运行中也强制还原（不推荐）")
    p.set_defaults(fn=cmd_restore)
    args = ap.parse_args()
    ensure_tools()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
