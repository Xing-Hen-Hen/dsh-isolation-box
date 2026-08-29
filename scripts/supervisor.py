#!/usr/bin/env python3
"""DSH 多实例插件框架 · 宿主 supervisor
职责：spawn 实例 / 心跳看门狗 / 进程组强杀 / 退避重启 / 熔断 / 崩溃取证 / 浏览器看板
零第三方依赖，仅标准库。

用法:
  python3 supervisor.py supervise --specs "demo1=plugin_demo.py"     # 监督一个或多个实例
  python3 supervisor.py approve <plugin.py>                          # 闸①: 实例冒烟测试通过才允许替换
  python3 supervisor.py board                                        # 浏览器看板 http://127.0.0.1:8765
  python3 supervisor.py status / logs <id> / kill <id> / disable <id>
"""
import argparse
import json
import os
import resource
import signal
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
# 实例根目录：默认 ~/project/instances，可用 $DSH_INSTANCES_ROOT 覆盖（他人部署免改源码）
INSTANCES_ROOT = os.environ.get("DSH_INSTANCES_ROOT", "/root/project/instances")
RUNNER = os.path.join(BASE, "instance_runner.py")

# 默认参数（已与用户确认）
DEFAULT_MEM_MB = 512
DEFAULT_CPU_SEC = 60
DEFAULT_WATCHDOG_SEC = 5
DEFAULT_HEARTBEAT_SEC = 2
DEFAULT_BACKOFF = [0.5, 1.0, 2.0, 4.0]
DEFAULT_MAX_CRASHES = 3


# ---------------------------------------------------------------- 工具
def inst_dir(inst_id):
    return os.path.join(INSTANCES_ROOT, inst_id)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def set_limits():
    """在实例进程 spawn 前执行的 rlimit 硬限制（子进程继承）。"""
    try:
        resource.setrlimit(resource.RLIMIT_AS, (DEFAULT_MEM_MB * 1024 * 1024,) * 2)
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (DEFAULT_CPU_SEC,) * 2)
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass


def tail(path, n=200):
    try:
        with open(path, "rb") as f:
            return f.read()[-8192:].decode(errors="replace").splitlines()[-n:]
    except Exception:
        return ["(无日志)"]


def open_browser(url):
    """自动调起移动端浏览器打开指定 URL（DSH 桥，零配置；缺失时打印手动地址）。"""
    try:
        import urllib.parse
        import urllib.request
        tok_file = os.environ.get("DSH_TOKEN_FILE") or os.path.join(
            os.environ.get("DSH_HOME", "/root/.dsh"), ".bridge_token")
        tok = open(tok_file).read().strip()
        q = urllib.parse.urlencode({"url": url, "token": tok})
        urllib.request.urlopen("http://127.0.0.1:3090/app/open?" + q, timeout=5)
        print(f"[supervisor] 🖥 浏览器已自动打开: {url}")
    except Exception as e:
        print(f"[supervisor] 自动打开浏览器失败（可手动访问 {url}）: {e}")


# ---------------------------------------------------------------- 实例监督
class Instance:
    def __init__(self, inst_id, plugin_path, max_crashes=DEFAULT_MAX_CRASHES):
        self.id = inst_id
        self.plugin = os.path.abspath(plugin_path)
        self.dir = inst_dir(inst_id)
        self.work = os.path.join(self.dir, "work")
        self.meta_path = os.path.join(self.dir, "meta.json")
        self.proc = None
        self.pgid = None
        self.spawned_at = 0     # 最近一次 spawn 时间（看门狗启动宽限期用）
        self.crashes = 0
        self.max_crashes = max_crashes
        self.fused = False          # 熔断
        self.done = False           # 正常成功
        self.last_exit = None
        self.meta = read_json(self.meta_path, {"restarts": 0, "crashes": [], "status": "created"})
        os.makedirs(self.work, exist_ok=True)

    def save_meta(self):
        write_json(self.meta_path, self.meta)

    def spawn(self):
        self.save_meta()
        self.spawned_at = time.time()
        stderr_log = open(os.path.join(self.dir, "stderr.log"), "ab")
        cmd = [sys.executable, RUNNER, "--plugin", self.plugin, "--dir", self.dir,
               "--heartbeat-sec", str(DEFAULT_HEARTBEAT_SEC)]
        self.proc = subprocess.Popen(
            cmd, stdout=stderr_log, stderr=stderr_log, cwd=self.work,
            start_new_session=True, preexec_fn=set_limits)
        self.pgid = self.proc.pid
        self.meta["status"] = "running"
        self.meta["pid"] = self.proc.pid
        self.meta["started_at"] = time.time()
        self.save_meta()
        print(f"[supervisor] {self.id}: spawn pid={self.proc.pid} 插件={os.path.basename(self.plugin)}")

    def heartbeat_age(self):
        st = read_json(os.path.join(self.dir, "state.json"))
        if not st:
            return 999
        return time.time() - st.get("heartbeat", 0)

    def kill_tree(self):
        if self.pgid:
            try:
                os.killpg(self.pgid, signal.SIGKILL)
            except Exception:
                pass
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass

    def crash(self, reason):
        self.crashes += 1
        self.meta["restarts"] = self.meta.get("restarts", 0) + 1
        self.meta["crashes"].append({"at": time.time(), "reason": reason})
        self.meta["crashes"] = self.meta["crashes"][-30:]   # 只留最近 30 条，防 meta.json 无限膨胀
        self.meta["status"] = "fused" if self.crashes >= self.max_crashes else "crashed"
        self.meta["last_crash_reason"] = reason
        self.save_meta()
        print(f"[supervisor] {self.id}: 崩溃#{self.crashes} reason={reason}")

    def tick(self):
        """每轮看门狗检查。返回 True 表示需要离开（成功或熔断）。"""
        if self.proc is None:
            return False
        # 1) 进程退出？
        rc = self.proc.poll()
        if rc is not None:
            self.last_exit = rc
            self.proc = None
            if rc == 0:
                self.meta["status"] = "completed"
                self.save_meta()
                print(f"[supervisor] {self.id}: 正常完成 ✅ (退出码 0)")
                self.done = True
                return True
            self.crash(f"exit_code={rc}")
            return True
        # 2) 心跳超时？（含启动宽限期：spawn 后先给 runner 足量的首个心跳写盘时间，
        #    避免容器卡顿时 state.json 未落盘被误杀、误计崩溃）
        if time.time() - self.spawned_at < DEFAULT_WATCHDOG_SEC:
            return False
        age = self.heartbeat_age()
        if age > DEFAULT_WATCHDOG_SEC:
            self.kill_tree()
            self.crash(f"watchdog_heartbeat_timeout={age:.1f}s")
            return True
        return False

    def poll(self):
        if self.done or self.fused:
            return
        if self.proc is None:
            backoff = DEFAULT_BACKOFF[min(self.crashes - 1, len(DEFAULT_BACKOFF) - 1)]
            if self.crashes >= self.max_crashes:
                self.fused = True
                self.meta["status"] = "fused"
                self.meta["fuse_reason"] = f"连续 {self.crashes} 次崩溃，已熔断，等待人工处理"
                self.save_meta()
                print(f"[supervisor] {self.id}: 🔥 熔断（连续 {self.crashes} 崩），停止自动重启 → 需人工")
                return
            print(f"[supervisor] {self.id}: 退避 {backoff}s 后重启 (第 {self.crashes + 1} 次尝试)")
            time.sleep(backoff)
            self.spawn()


# ---------------------------------------------------------------- 子命令
def cmd_supervise(args):
    specs = []
    for s in args.specs:
        inst_id, path = s.split("=", 1)
        specs.append((inst_id, path))
    instances = [Instance(i, p, args.max_crashes) for i, p in specs]
    for it in instances:
        it.spawn()
    print(f"[supervisor] 正在监督 {len(instances)} 个实例，看门狗 {DEFAULT_WATCHDOG_SEC}s / 心跳 {DEFAULT_HEARTBEAT_SEC}s")
    alive = list(instances)
    while alive:
        time.sleep(0.5)
        for it in alive:
            it.tick()
        for it in alive:
            it.poll()
        alive = [it for it in instances if not (it.done or it.fused)]
        # 全部熔断/完成则退出（也可 --watch 保持常驻；保持简单：退出，看板仍可用）
    print("[supervisor] 监督循环结束")


def cmd_approve(args):
    """闸①：在实例中做加载冒烟测试（runner --smoke），通过才允许写正式目录。"""
    inst_id = f"approve-{int(time.time())}"
    it = Instance(inst_id, args.plugin)
    # 冒烟: 让 runner 只加载+init 不 run
    stderr_log = open(os.path.join(it.dir, "stderr.log"), "ab")
    cmd = [sys.executable, RUNNER, "--plugin", it.plugin, "--dir", it.dir,
           "--heartbeat-sec", str(DEFAULT_HEARTBEAT_SEC), "--smoke"]
    it.proc = subprocess.Popen(cmd, stdout=stderr_log, stderr=stderr_log,
                               cwd=it.work, start_new_session=True, preexec_fn=set_limits)
    it.pgid = it.proc.pid
    deadline = time.time() + 20
    while time.time() < deadline:
        rc = it.proc.poll()
        if rc is not None:
            break
        time.sleep(0.2)
    else:
        it.kill_tree()
        print(f"[supervisor] {args.plugin} 冒烟超时 → ✗ 不可替换（问题：初始化挂死）")
        return 1
    res = read_json(os.path.join(it.dir, "result.json"))
    ok = rc == 0 and res and res.get("ok")
    if ok:
        it.meta["status"] = "completed"
        it.save_meta()
        print(f"[supervisor] ✅ 冒烟通过：{args.plugin} 可在实例环境完整加载+初始化")
        print("   → 现在允许: 备份旧版 → 替换正式目录 → 重启生效")
        return 0
    # 失败原因优先取实例侧落盘的 error（runner 异常时写 state.json），
    # 比裸 exit_code 更能定位问题
    st = read_json(os.path.join(it.dir, "state.json"), {})
    reason = (res or {}).get("error") or st.get("error") or f"exit_code={rc}"
    print(f"[supervisor] ✗ 冒烟未通过：{args.plugin} → {reason}")
    print(f"   → 禁止替换正式目录。崩溃现场见 {os.path.join(it.dir, 'stderr.log')}")
    return 1


def cmd_status(args):
    print(f"{'ID':<20}{'状态':<12}{'心跳':<8}{'重启':<6}{'备注'}")
    names = sorted(os.listdir(INSTANCES_ROOT)) if os.path.isdir(INSTANCES_ROOT) else []
    for name in names:
        d = inst_dir(name)
        st = read_json(os.path.join(d, "state.json"), {})
        meta = read_json(os.path.join(d, "meta.json"), {})
        hb = st.get("heartbeat")
        age = "-" if hb is None else f"{time.time() - hb:.0f}s"
        note = meta.get("fuse_reason") or meta.get("last_crash_reason") or st.get("status", "?")
        print(f"{name:<20}{meta.get('status', st.get('status', '?')):<12}{age:<8}{meta.get('restarts', 0):<6}{note or ''}")


def cmd_logs(args):
    for line in tail(os.path.join(inst_dir(args.id), "stderr.log")):
        print(line)


def cmd_kill(args):
    it = Instance(args.id, "")
    p = read_json(os.path.join(it.dir, "meta.json"), {}).get("pid")
    if p:
        try:
            os.killpg(p, signal.SIGKILL)
            print(f"[supervisor] {args.id}: 已强杀进程组 {p}")
        except Exception as e:
            print(f"[supervisor] {args.id}: kill 失败 {e}")
    else:
        print(f"[supervisor] {args.id}: 无运行中进程")


def cmd_disable(args):
    meta = read_json(os.path.join(inst_dir(args.id), "meta.json"), {})
    meta["status"] = "disabled"
    meta["fuse_reason"] = "人工禁用（dsh-guard 一键熔断）"
    write_json(os.path.join(inst_dir(args.id), "meta.json"), meta)
    print(f"[supervisor] {args.id}: 已禁用")


# ---------------------------------------------------------------- 看板
def board_data():
    rows = []
    if os.path.isdir(INSTANCES_ROOT):
        for name in sorted(os.listdir(INSTANCES_ROOT)):
            d = inst_dir(name)
            st = read_json(os.path.join(d, "state.json"), {})
            meta = read_json(os.path.join(d, "meta.json"), {})
            hb = st.get("heartbeat")
            age = None if hb is None else round(time.time() - hb, 1)
            status = meta.get("status") or st.get("status", "?")
            result = read_json(os.path.join(d, "result.json"))
            if status == "completed" and result and result.get("ok"):
                note = "✅ " + str(result.get("result", {}).get("msg", "执行成功"))
            elif status == "completed":
                note = "完成"
            else:
                note = (meta.get("fuse_reason") or meta.get("last_crash_reason") or st.get("error") or "")
            rows.append({
                "id": name, "status": status, "heartbeat_age": age,
                "restarts": meta.get("restarts", 0),
                "crashes_len": len(meta.get("crashes", [])),
                "note": note[:200],
                "detail": st.get("detail", ""),
                "result": result,
            })
    return rows


def cmd_board(args):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="3"><title>DSH 多实例看板</title><style>
body{font-family:monospace;background:#111;color:#0f0;padding:16px}h1{color:#fff;font-size:18px}
small{color:#888}table{border-collapse:collapse;width:100%;margin-top:8px}
th{color:#aaa;border:1px solid #333;padding:6px}td{border:1px solid #333;padding:6px}
.running,.completed{color:#0f0}.crashed{color:#f33}.fused,.disabled{color:#ff0}
</style></head><body><h1>DSH 多实例看板 <small>3s 自动刷新 · 数据源 supervisor</small></h1>
<table><tr><th>ID</th><th>状态</th><th>心跳(s)</th><th>重启</th><th>备注</th></tr>
{rows}</table><small>listen 127.0.0.1:8765 · 仅本机 · 死循环/内存泄漏由 rlimit 与看门狗兜底</small>
</body></html>"""
    rows_html, status_color = [], {"running": "running", "completed": "completed",
                                   "crashed": "crashed", "fused": "fused", "disabled": "disabled"}
    for r in board_data():
        cls = status_color.get(r["status"], "crashed")
        rows_html.append(
            f'<tr><td>{r["id"]}</td><td class="{cls}">{r["status"]}</td>'
            f'<td>{r["heartbeat_age"] if r["heartbeat_age"] is not None else "-"}</td>'
            f'<td>{r["restarts"]}</td><td>{r["note"]}</td></tr>')
    page = PAGE.replace("{rows}", "\n".join(rows_html)).encode()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api":
                body = json.dumps(board_data(), ensure_ascii=False).encode()
            else:
                body = page
            self.send_response(200)
            self.send_header("Content-Type", "application/json" if self.path == "/api" else "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # 防浏览器缓存旧快照（看板数据必须每 3s 拿最新）
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    print("[supervisor] 看板: http://127.0.0.1:8765/ （Ctrl+C 停止；实例不受影响）")
    if getattr(args, "open", False):
        open_browser("http://127.0.0.1:8765/")
    HTTPServer(("127.0.0.1", 8765), H).serve_forever()


# ---------------------------------------------------------------- 保险（v0.1.3）
def auto_backup(reason="supervisor"):
    """启动时默认自动备份会话（--no-backup 可关）。明确打印备份目录与还原方法。"""
    try:
        r = subprocess.run([sys.executable, os.path.join(BASE, "backup.py"),
                            "sessions", "--reason", reason],
                           capture_output=True, text=True, timeout=120)
        for line in (r.stdout or "").splitlines():
            if line.startswith("[backup]") or line.startswith("BACKUP_DIR="):
                print(line, flush=True)
    except Exception as e:
        print(f"[supervisor] ⚠️ 自动备份失败（继续运行）: {e}")


def cmd_stop_all(args):
    """停止全部实例进程 + 看板服务。校验 instance_runner 零残留。"""
    import signal as _sig
    stopped = []
    if os.path.isdir(INSTANCES_ROOT):
        for name in sorted(os.listdir(INSTANCES_ROOT)):
            meta = read_json(os.path.join(inst_dir(name), "meta.json"), {})
            pid = meta.get("pid")
            if pid:
                try:
                    os.killpg(pid, _sig.SIGKILL)
                    stopped.append(f"{name}(pid={pid})")
                except Exception:
                    pass
    # 看板服务（8765）
    out = subprocess.run(["pgrep", "-f", "supervisor[.]py board"],
                         capture_output=True, text=True).stdout.strip()
    for p in out.splitlines():
        if p.isdigit():
            try:
                os.killpg(int(p), _sig.SIGKILL)
                stopped.append(f"board(pid={p})")
            except Exception:
                pass
    # 校验实例骨架零残留
    left = subprocess.run(["pgrep", "-f", "instance_runner[.]py"],
                          capture_output=True, text=True).stdout.strip()
    left = [p for p in left.splitlines() if p.isdigit()]
    print(f"[supervisor] stop-all: 已停止 {len(stopped)} 个进程组: {', '.join(stopped) or '无'}")
    if left:
        print(f"[supervisor] ⚠️ 仍有实例进程残留: {','.join(left)}（请人工确认）")
    else:
        print("[supervisor] ✅ 实例/看板进程已全部停止（instance_runner 零残留）")
    return 0


def cmd_finalize(args):
    """最后一步：强制备份会话（含当前对话）→ 停全部进程 → 询问是否安装进主进程。

    安装动作由宿主（Agent/用户）用 dsh plugin add 完成；本命令保证「询问前
    所有对话已备份、所有实例进程已停止」，确认后引导安全重启（safe_restart）。
    """
    print("═══ 最后一步：安装到主 DSH ═══")
    # ① 强制备份（含当前对话）
    print("→ ① 备份所有会话（含当前对话）…")
    auto_backup(reason="finalize")
    # ② 停止全部实例/看板
    print("→ ② 停止全部实例进程与看板…")
    cmd_stop_all(args)
    # ③ 指引
    print("→ ③ 安装指引（确认后由你/宿主执行）:")
    if getattr(args, "plugin", None):
        print(f"     dsh plugin --profile web add file:{os.path.abspath(args.plugin)}")
    else:
        print("     dsh plugin --profile web add <包名或路径>")
    print("→ ④ 还原方法（出事时）:")
    print("     python3 backup.py restore <备份名>   # backup.py list 查看")
    print("     python3 safe_restart.py --check      # 重启后校验")
    # ④ 询问
    ans = input("⚠️ 确认把插件写入主进程并安全重启？[y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        print("[supervisor] 已取消（备份与进程停止已生效，环境处于安全态）")
        return 1
    # ⑤ 安全重启
    print("→ ⑤ 执行安全重启（safe_restart）…")
    r = subprocess.run([sys.executable, os.path.join(BASE, "safe_restart.py"), "--yes"],
                       text=True)
    print("[supervisor] finalize 完成，返回码 %d" % r.returncode)
    return r.returncode


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="DSH 多实例宿主 supervisor")
    ap.add_argument("--no-backup", action="store_true",
                    help="关闭启动时的会话自动备份")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("supervise")
    p.add_argument("--specs", action="append", required=True, help="id=plugin_path，可重复")
    p.add_argument("--max-crashes", type=int, default=DEFAULT_MAX_CRASHES)
    p.set_defaults(fn=cmd_supervise)
    p = sub.add_parser("approve")
    p.add_argument("plugin", help="待验收插件路径（闸① 冒烟测试）")
    p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("status")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("board")
    p.add_argument("--open", action="store_true", help="启动看板后自动打开浏览器（默认不开）")
    p.set_defaults(fn=cmd_board)
    p = sub.add_parser("logs")
    p.add_argument("id")
    p.set_defaults(fn=cmd_logs)
    p = sub.add_parser("kill")
    p.add_argument("id")
    p.set_defaults(fn=cmd_kill)
    p = sub.add_parser("disable")
    p.add_argument("id")
    p.set_defaults(fn=cmd_disable)
    p = sub.add_parser("stop-all")
    p.set_defaults(fn=cmd_stop_all)
    p = sub.add_parser("finalize")
    p.add_argument("--plugin", default="", help="待安装插件路径（仅指引用）")
    p.set_defaults(fn=cmd_finalize)
    args = ap.parse_args()
    # 启动自动备份（除 finalize 自身已强制备份、stop-all/status 等轻量命令外默认开启）
    if not args.no_backup and args.cmd not in ("finalize", "stop-all", "status", "logs", "list"):
        auto_backup(reason="supervisor:" + args.cmd)
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
