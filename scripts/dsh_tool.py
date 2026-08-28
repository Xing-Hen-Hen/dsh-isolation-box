#!/usr/bin/env python3
"""dsh-tool —— 完整 DSH 实例管理器（「两者结合」的完整实例层）
每个实例 = 独立 profile（与主实例完全同构：core + 用户插件栈）
       + 独立端口 + 独立进程 + 完整 GUI（浏览器直达）

用法:
  python3 dsh_tool.py up <name> [--port 3082]   # 拉起完整 DSH 实例（自动同构/自动选端口）
  python3 dsh_tool.py down <name>               # 停止实例（保留 profile 目录）
  python3 dsh_tool.py status                    # 列出全部实例（端口/pid/URL）
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

HOME = os.environ.get("DSH_HOME", "/root/.dsh")
PROFILES = os.path.join(HOME, "profiles")
TEMPLATE = os.path.join(PROFILES, "web")          # 主 profile 模板（参考）
REG = os.path.join(HOME, ".dsh-tool-registry.json")
DSH_BIN = "/usr/local/lib/node_modules/@deepseek-ai/dsh/lib/bin.js"
LOG_DIR = os.environ.get("DSH_TOOL_LOG_DIR", os.path.expanduser("~/.dsh-tool-logs"))
USER_PLUGINS = ["dsh-device-shell-guide", "dsh-status-overlay", "dsh-task-notifier"]


def token():
    try:
        tok_file = os.environ.get("DSH_TOKEN_FILE") or os.path.join(HOME, ".bridge_token")
        return open(tok_file).read().strip()
    except Exception:
        return ""


def registry():
    if os.path.exists(REG):
        try:
            return json.load(open(REG))
        except Exception:
            pass
    return {}


def save_reg(r):
    tmp = REG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(r, f, indent=1, ensure_ascii=False)
    os.replace(tmp, REG)


def url_of(port):
    return f"http://127.0.0.1:{port}/?dsha_t={token()}"


def open_browser(url):
    """自动调起移动端浏览器打开 URL（DSH 桥；缺失时打印手动地址）。"""
    try:
        import urllib.parse
        import urllib.request
        q = urllib.parse.urlencode({"url": url, "token": token()})
        urllib.request.urlopen("http://127.0.0.1:3090/app/open?" + q, timeout=5)
        print(f"[dsh-tool] 🖥 浏览器已自动打开: {url}")
    except Exception as e:
        print(f"[dsh-tool] 自动打开浏览器失败（可手动访问 {url}）: {e}")


def running_pids(name):
    """找到已运行的同名实例进程（正则防误匹配其他进程）。"""
    out = subprocess.run(["pgrep", "-f", f"lib/bin[.]js --profile {name} "],
                         capture_output=True, text=True).stdout.strip()
    return [p for p in out.splitlines() if p.isdigit()]


def mk_profile(name):
    d = os.path.join(PROFILES, name)
    if os.path.isdir(d):
        return d
    os.makedirs(d)
    for f in ("package.json", "pnpm-workspace.yaml", ".npmrc", "cordis.yml"):
        src = os.path.join(TEMPLATE, f)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(d, f))
    nm = os.path.join(d, "node_modules")
    if not os.path.exists(nm):
        os.symlink("../node_modules", nm)            # 复用 workspace 根 hoist 依赖
    for p in USER_PLUGINS:                          # DSHA 隐藏用户插件以正式名暴露
        src = os.path.join(TEMPLATE, "node_modules", ".ignored_" + p)
        dest = os.path.join(nm, p)
        if os.path.isdir(src) and not os.path.exists(dest):
            os.symlink(src, dest)
    os.makedirs(os.path.join(nm, "@dsh-external"), exist_ok=True)
    mobile = os.path.join(nm, "@dsh-external", "dsh-mobile-nav")
    mobile_src = os.environ.get("DSH_MOBILE_NAV_SRC", "/root/dsha-mobile-nav")
    if not os.path.exists(mobile) and os.path.isdir(mobile_src):
        os.symlink(mobile_src, mobile)
    return d


def wait_ready(port, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            import urllib.request
            r = urllib.request.urlopen(url_of(port), timeout=3)
            if r.status == 200:
                return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def cmd_up(args):
    name = args.name
    os.makedirs(LOG_DIR, exist_ok=True)
    d = mk_profile(name)
    pids = running_pids(name)
    if pids:
        print(f"[dsh-tool] {name} 已在运行 (pid={','.join(pids)})")
        print(f"  访问: {url_of(args.port)}")
        print(f"  如要重启请先 down: python3 dsh_tool.py down {name}")
        return 0
    log = open(os.path.join(LOG_DIR, f"{name}.log"), "ab")
    proc = subprocess.Popen(
        ["node", DSH_BIN, "--profile", name, "--port", str(args.port), "--no-open"],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    print(f"[dsh-tool] {name}: 已拉起 pid={proc.pid} 端口={args.port}（等待就绪…）")
    if wait_ready(args.port):
        print(f"[dsh-tool] ✅ {name} 就绪！浏览器访问: {url_of(args.port)}")
        print("  移动端浏览器打开后即为完整 DSH 界面；可小窗悬浮围观")
        if not getattr(args, "no_open", False):
            open_browser(url_of(args.port))
    else:
        print(f"[dsh-tool] ⚠️ 40s 内未就绪，日志: {LOG_DIR}/{name}.log")
    reg = registry()
    reg[name] = {"port": args.port, "pid": proc.pid, "started_at": time.time()}
    save_reg(reg)
    return 0


def cmd_down(args):
    name = args.name
    pids = running_pids(name)
    if pids:
        for p in pids:
            try:
                os.killpg(int(p), signal.SIGKILL)
                print(f"[dsh-tool] {name}: 已强杀进程组 {p}")
            except Exception:
                try:
                    os.kill(int(p), signal.SIGKILL)
                except Exception as e:
                    print(f"[dsh-tool] kill {p} 失败: {e}")
    else:
        print(f"[dsh-tool] {name}: 未在运行")
    reg = registry()
    if name in reg:
        del reg[name]
        save_reg(reg)
    return 0


def cmd_status(args):
    reg = registry()
    print(f"{'实例':<14}{'端口':<7}{'PID':<9}{'状态':<8}访问URL")
    for name, info in sorted(reg.items()):
        url = url_of(info.get("port", "?"))
        print(f"{name:<14}{info.get('port', '-'):<7}{info.get('pid', '-'):<9}运行中   {url}")
    if not reg:
        print("(无运行中的完整实例。dsh-tool up <name> 拉起一个)")
    # 顺带列出轻量执行器
    print("\n轻量执行器实例（看板）: supervisor.py status / 看板 http://127.0.0.1:8765/")


def cmd_publish(args):
    """发布唯一入口：组装包 → 五道预检（强制）→ 通过才产 tar.gz。预检不过 = 禁发。"""
    import shutil
    src = os.path.abspath(args.src)
    name = args.name
    if not os.path.isfile(os.path.join(src, "package.json")):
        print(f"[publish] ❌ {src} 不是插件目录（无 package.json）")
        return 1
    work = "/tmp/dsh-publish"
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)
    shutil.copytree(src, os.path.join(work, name))     # 顶层目录名 = 包名（App 注册名规则）
    tarball = os.path.join(os.environ.get("DSH_EXPORT_DIR", "/sdcard/Download"), f"{name}-v{args.version}.tar.gz")
    r = subprocess.run(["tar", "-C", work, "-czf", tarball, name], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[publish] ❌ 打包失败: {r.stderr[-200:]}")
        return 1
    checker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "release_check.py")
    cmd = ["python3", checker, "--tarball", tarball]
    if args.fingerprint:
        cmd += ["--expect-client-fingerprint", args.fingerprint]
    rc = subprocess.run(cmd, text=True)
    if rc.returncode != 0:
        print(f"[publish] ❌ 预检未通过——包 {tarball} 已产出但禁发（修复后重跑 publish）")
        return 1
    print(f"[publish] ✅ 五道预检全绿，可导入: {tarball}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="DSH 完整实例管理器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("up")
    p.add_argument("name")
    p.add_argument("--port", type=int, default=3082)
    p.add_argument("--no-open", action="store_true", help="拉起实例后不自动打开浏览器")
    p.set_defaults(fn=cmd_up)
    p = sub.add_parser("down")
    p.add_argument("name")
    p.set_defaults(fn=cmd_down)
    p = sub.add_parser("status")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("publish")
    p.add_argument("--src", required=True, help="插件源码目录（含 package.json）")
    p.add_argument("--name", required=True, help="插件名（= 包名 = 顶层目录名）")
    p.add_argument("--version", required=True)
    p.add_argument("--fingerprint", default="", help="客户端产物指纹（如 re-menu-in-up）")
    p.set_defaults(fn=cmd_publish)
    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
