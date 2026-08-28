#!/usr/bin/env python3
"""四幕自动化演示：完整验证多实例框架全链路。
第 1 幕 崩溃自愈    supervisor supervise demo1=plugin_demo.py   （崩2次 → 第3次成功）
第 2 幕 熔断止损    DEMO_MODE=crash_all supervise demo2        （连崩3次 → 熔断）
第 3 幕 验收拦截    supervisor approve bad_plugin.py (拒) / plugin_demo.py (过)
第 4 幕 启动守卫    dsh_guard start (失败→回滚→成功；失败→安全模式)
"""
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/root/project"
INSTANCES = os.path.join(ROOT, "instances")
SIM = os.path.join(BASE, ".sim")


def sh(cmd, env=None, timeout=120):
    print(f"$ {cmd}", flush=True)
    e = dict(os.environ, **(env or {}))
    r = subprocess.run(cmd, shell=True, env=e, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout + r.stderr).strip()
    if out:
        print(out, flush=True)
    return r.returncode, out


def clean():
    """清场只删演示自身的产物（前缀匹配），绝不整目录 rmtree ——
    防止 instances/ 里混入非演示实例数据时被误删（SAFETY.md 防自杀纪律）。"""
    for d in [INSTANCES, SIM]:
        if os.path.isdir(d):
            for name in os.listdir(d):
                if name.startswith(("demo", "approve-")) or name in ("start_count.txt", "READY"):
                    p = os.path.join(d, name)
                    if os.path.isdir(p) and not os.path.islink(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        try:
                            os.remove(p)
                        except OSError:
                            pass
    os.makedirs(INSTANCES, exist_ok=True)
    os.makedirs(SIM, exist_ok=True)


def act(title, fn):
    print("\n" + "=" * 66)
    print(f"  {title}")
    print("=" * 66, flush=True)
    fn()


def main():
    clean()
    demo1 = os.path.join(INSTANCES, "demo1")

    # ---------- 第 1 幕 ----------
    def act1():
        rc, out = sh(f"python3 {BASE}/supervisor.py supervise --specs demo1={BASE}/plugin_demo.py")
        print(">>> 结论: ", end="")
        if "正常完成" in out:
            import json
            res = json.load(open(os.path.join(demo1, "result.json")))
            print(f"实例自动重启 2 次后自愈成功 ✅  result={res['result']['tries']}次执行最终成功")
        else:
            print("❌ 未按预期完成")
            sys.exit(1)

    act("第 1 幕 · 崩溃自愈：坏插件连崩 2 次，第 3 次自动成功", act1)

    # ---------- 第 2 幕 ----------
    def act2():
        rc, out = sh(f"DEMO_MODE=crash_all python3 {BASE}/supervisor.py supervise --specs demo2={BASE}/plugin_demo.py")
        print(">>> 结论: ", end="")
        if "熔断" in out and "需人工" in out:
            print("连续 3 崩 → 熔断止损 ✅（不无限重启，停在「需人工」）")
        else:
            print("❌ 熔断未触发")
            sys.exit(1)

    act("第 2 幕 · 熔断止损：永远坏的插件连崩 3 次，看门狗熔断", act2)

    # ---------- 第 3 幕 ----------
    def act3():
        rc, out = sh(f"python3 {BASE}/supervisor.py approve {BASE}/bad_plugin.py")
        blocked = "冒烟未通过" in out and "禁止替换" in out
        rc2, out2 = sh(f"python3 {BASE}/supervisor.py approve {BASE}/plugin_demo.py")
        passed = "冒烟通过" in out2 and "允许" in out2
        print(">>> 结论: ", end="")
        if blocked and passed:
            print("坏插件被拦截 ❌不替换，好插件放行 ✅ —— 闸① 验收门槛生效")
        else:
            print("❌ 验收逻辑异常", blocked, passed)
            sys.exit(1)

    act("第 3 幕 · 验收拦截：坏插件冒烟失败 → 禁止替换；好插件 → 放行", act3)

    # ---------- 第 4 幕 ----------
    def act4():
        sh(f"python3 {BASE}/dsh_guard.py backup")   # 闸①通过后先备份旧版（正式流程第一式）
        rc, out = sh(f"python3 {BASE}/dsh_guard.py start --cmd 'python3 {BASE}/dsh_sim.py --fail-once --exit-after 4' --timeout 15")
        if "回滚后启动成功" not in out:
            print(">>> 结论 ❌ 回滚重试未生效"); print(out[-2000:]); sys.exit(1)

        rc2, out2 = sh(f"python3 {BASE}/dsh_guard.py start --cmd 'python3 {BASE}/dsh_sim.py --fail-always --exit-after 4' --timeout 15")
        if "安全模式启动成功" not in out2:
            print(">>> 结论 ❌ 安全模式未触发"); print(out2[-2000:]); sys.exit(1)
        print(">>> 结论: 坏插件 → 回滚重试 → 成功 ✅；回滚也救不回 → 安全模式必起 ✅（GUI 永远打得开）")

    act("第 4 幕 · 启动守卫：模拟坏插件导致 DSH 启动失败 → 自动回滚 / 安全模式", act4)

    print("\n" + "=" * 66)
    print("  ✅ 四幕演示全部通过 —— 完整链路验证成功")
    print("=" * 66)


if __name__ == "__main__":
    main()
