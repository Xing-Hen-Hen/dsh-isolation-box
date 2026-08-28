#!/usr/bin/env python3
"""迷你状态看板（原型验证）——演示实例运行状态如何用浏览器查看。
仅用标准库。监听 127.0.0.1:8765。生产版由 supervisor 提供数据源。"""
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# 模拟的实例状态（生产版这里换成读 supervisor 的实例目录）
INSTANCES = [
    {"id": "test-001", "status": "running",  "heartbeat": "2s ago",  "cpu": "3%",  "mem": "42MB", "note": "心跳正常"},
    {"id": "test-002", "status": "crashed",  "heartbeat": "OFF",     "cpu": "-",   "mem": "-",    "note": "第2次崩溃, stderr尾部见下"},
    {"id": "test-003", "status": "restarted", "heartbeat": "5s ago", "cpu": "1%",  "mem": "38MB", "note": "退避2s后已重启"},
]

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="3">
<title>DSH 多实例看板</title><style>
body{font-family:monospace;background:#111;color:#0f0;padding:20px}
table{border-collapse:collapse;width:100%}
td,th{border:1px solid #333;padding:8px 12px;text-align:left}
.running{color:#0f0}.crashed{color:#f33}.restarted{color:#ff0}
h1{color:#fff}small{color:#888}</style></head><body>
<h1>DSH 多实例看板 <small>自动刷新 3s</small></h1>
<table><tr><th>实例ID</th><th>状态</th><th>心跳</th><th>CPU</th><th>内存</th><th>备注</th></tr>
""" + "".join(
    f'<tr><td>{i["id"]}</td><td class="{i["status"]}">{i["status"]}</td>'
    f'<td>{i["heartbeat"]}</td><td>{i["cpu"]}</td><td>{i["mem"]}</td><td>{i["note"]}</td></tr>'
    for i in INSTANCES
) + "</table><small>页面由实例看板服务提供 · 仅监听 127.0.0.1</small></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(INSTANCES).encode() if self.path == "/api" else PAGE.encode()
        ctype = "application/json" if self.path == "/api" else "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静默访问日志
        pass


if __name__ == "__main__":
    print("看板服务已启动: http://127.0.0.1:8765", flush=True)
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
