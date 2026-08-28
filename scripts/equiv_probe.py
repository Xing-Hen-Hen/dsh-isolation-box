#!/usr/bin/env python3
"""环境等价性探针：宿主 vs 实例 输出对比。
证明：实例与宿主共享同一运行时/依赖/工具/接口面。"""
import json
import subprocess
import sys


def probe():
    info = {}
    # 1) 运行时
    info["python"] = sys.version.split()[0]
    info["sys.executable"] = sys.executable
    # 2) 第三方依赖面：同样的 import 结果
    mods = {}
    for m in ["json", "resource", "fcntl", "signal", "socket", "multiprocessing",
              "http.server", "subprocess", "ctypes", "sqlite3"]:
        try:
            __import__(m)
            mods[m] = "OK"
        except Exception as e:
            mods[m] = f"FAIL:{e}"
    info["stdlib_imports"] = mods
    # 3) 工具链
    tools = {}
    for t in ["python3", "node", "curl", "bash", "git", "timeout", "setsid"]:
        try:
            r = subprocess.run(["which", t], capture_output=True, text=True, timeout=5)
            tools[t] = r.stdout.strip() or "无"
        except Exception as e:
            tools[t] = f"ERR:{e}"
    info["tools"] = tools
    # 4) 环境变量面
    keys = sorted(k for k in ("PATH", "PYTHONPATH", "HOME", "LANG", "SHELL", "TERM") if k in __import__("os").environ)
    info["env_keys"] = keys
    # 5) 接口面：DSH 桥可达性
    try:
        import urllib.request
        tok = open("/root/.dsh/.bridge_token").read().strip()
        r = urllib.request.urlopen(
            "http://127.0.0.1:3090/app/device?token=" + tok, timeout=5)
        info["dsh_bridge"] = "可达" + r.read().decode()[:60]
    except Exception as e:
        info["dsh_bridge"] = f"FAIL:{e}"
    return info


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False, indent=1))
