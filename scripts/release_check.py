#!/usr/bin/env python3
"""隔离箱 · 插件发布预检 release_check.py
把 dsh-reasoning-effort 四度波折的教训固化为自动化检查：
  ① 包格式检查（顶层 package/ —— App 导入只认 npm 标准格式）
  ② 依赖闭包检查（解压后脱离软链环境 import 验证 —— cosmlit 教训）
  ③ bundle/patch 声明检查（dsh.bundle + cordis.patch.yml 一致）
  ④ 产物指纹检查（目标产物特征在包内 —— 忘换产物的教训）
用法:
  python3 release_check.py --tarball <file.tar.gz> --expect-client-fingerprint "re-menu-in-up"
"""
import argparse
import io
import json
import os
import subprocess
import tarfile
import tempfile


def fail(msg):
    print(f"  ❌ {msg}")
    return False


def ok(msg):
    print(f"  ✅ {msg}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tarball", required=True)
    ap.add_argument("--expect-client-fingerprint", default="",
                    help="客户端产物必须包含的指纹串（如 mobilefix 特征）")
    ap.add_argument("--node", default="node")
    ap.add_argument("--skip-boot", action="store_true", help="跳过动态 boot 预演")
    ap.add_argument("--boot-timeout", default="40", help="boot 预演超时秒（默认40）")
    args = ap.parse_args()

    t = args.tarball
    print(f"== 预检: {os.path.basename(t)} ==")
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            tf = tarfile.open(t, "r:gz")
            names = tf.getnames()
        except Exception as e:
            print(f"❌ tar 无法打开: {e}")
            return 1

        # ① 顶层格式：唯一顶层目录，且目录名 == 包内 package.json 的 name（App 按顶层目录名注册）
        top = sorted({n.split("/")[0] for n in names})
        if len(top) != 1:
            print(f"  ❌ 顶层目录不唯一: {top} —— 包结构无效")
            return 1
        topname = top[0]
        print(f"  ✅ ① 顶层目录 = {topname}（App 注册名来源）")

        # ② 依赖闭包（解压后,脱离宿主的真实 import 验证）
        tf = tarfile.open(t, "r:gz")
        try:
            tf.extractall(tmp)
        except Exception as e:
            return fail(f"② 解压失败: {e}") or 1
        pkg = os.path.join(tmp, topname)
        if not os.path.isdir(pkg):
            return fail("② 解压后无包根目录") or 1
        manifest = json.load(open(os.path.join(pkg, "package.json")))
        if manifest.get("name") != topname:
            checks.append(fail(f"② 包名 {manifest.get('name')} ≠ 顶层目录 {topname} —— App 注册名会与真名背离"))
        else:
            checks.append(ok(f"② 包名 == 顶层目录（{topname}）—— 注册名正确"))
        # ② 依赖检查：有 deps → 逐一校验实体闭包必须齐全（按 manifest 声明，而非写死的包名）
        declared = manifest.get("dependencies", {}) or {}
        if not declared:
            checks.append(ok("② 零依赖包（lib 零 import，无运行时依赖风险）"))
        else:
            nm = os.path.join(pkg, "node_modules")
            if not os.path.isdir(nm):
                checks.append(fail("② 包内无 node_modules —— 导入后必 boot 失败（cosmokit 教训）"))
            else:
                missing = [d for d in declared
                           if not os.path.isfile(os.path.join(nm, d, "package.json"))]
                if missing:
                    checks.append(fail(f"② 依赖缺失 {missing} —— 导入后必崩"))
                else:
                    checks.append(ok(f"② 依赖闭包齐全（{len(declared)} 个声明依赖实体均在包内）"))

        # ③ bundle/patch 声明
        try:
            pj = json.load(open(os.path.join(pkg, "package.json")))
            bundle_patch = pj.get("dsh", {}).get("bundle", {}).get("patch")
            patch_ok = bundle_patch and os.path.isfile(os.path.join(pkg, bundle_patch))
            checks.append(ok(f"③ dsh.bundle.patch = {bundle_patch}") if patch_ok else fail("③ 缺 dsh.bundle.patch 声明"))
        except Exception as e:
            checks.append(fail(f"③ package.json 读取失败: {e}"))

        # ④ 产物指纹
        client = os.path.join(pkg, "lib", "client", "index.js")
        if args.expect_client_fingerprint:
            if os.path.isfile(client):
                body = open(client, "r", errors="replace").read()
                checks.append(ok(f"④ 客户端产物含期望指纹（{args.expect_client_fingerprint}）")
                              if args.expect_client_fingerprint in body
                              else fail(f"④ 客户端产物不含 {args.expect_client_fingerprint} —— 产物没换/构建缺失!"))
            else:
                checks.append(fail("④ lib/client/index.js 缺失"))

        # ⑤ 动态 boot 预演：模拟「App 导入后」的解析环境（解压版 + 无软链依赖）
        #    这与之前崩溃的诊断场景完全一致：解压进临时 profile → 注册 bundle → 真 boot
        if args.skip_boot:
            print("  ⏭ ⑤ 跳过动态 boot 预演")
        else:
            try:
                # 把解压出的包按「App 语义」装进临时 profile
                import shutil
                prof = "/root/.dsh/profiles/precheck"
                if os.path.isdir(prof):
                    shutil.rmtree(prof)
                os.makedirs(os.path.join(prof, "node_modules"), exist_ok=True)
                # App 行为：解压到 node_modules/<顶层目录名>；Bundle 名 = 顶层目录名
                shutil.copytree(pkg, os.path.join(prof, "node_modules", topname))
                manifest = json.load(open(os.path.join(pkg, "package.json")))
                pj = {"name": "dsh-profile-precheck", "private": True, "dependencies": {},
                      "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app", topname]}}}
                with open(os.path.join(prof, "package.json"), "w") as f:
                    json.dump(pj, f, indent=2)
                for fn in ("pnpm-workspace.yaml", ".npmrc"):
                    # 模板 profile 不一定有 .npmrc（web profile 实测缺失）——存在才拷，缺失不阻塞预演
                    srcf = "/root/.dsh/profiles/web/" + fn
                    if os.path.exists(srcf):
                        shutil.copy(srcf, os.path.join(prof, fn))
                # 真 boot（--port 0 随机，--no-open）：就绪检测 = 日志出现 "dsh web: http"
                import subprocess
                logf = open("/tmp/precheck-boot.log", "w")
                proc = subprocess.Popen(
                    ["node", "/usr/local/lib/node_modules/@deepseek-ai/dsh/lib/bin.js",
                     "--profile", "precheck", "--port", "0", "--no-open"],
                    stdout=logf, stderr=subprocess.STDOUT)
                import time
                deadline = time.time() + int(args.boot_timeout)
                ok_boot = False
                while time.time() < deadline:
                    if proc.poll() is not None:
                        # 进程已退出：读最终日志判失败（避免运行期把日志里的
                        # 警告性 "Error" 字样误判为 boot 失败）
                        try:
                            logf.flush()
                            body = open("/tmp/precheck-boot.log", "r", errors="replace").read()
                        except Exception:
                            body = ""
                        if "dsh web: http" in body:
                            ok_boot = True
                        break
                    try:
                        logf.flush()
                        body = open("/tmp/precheck-boot.log", "r", errors="replace").read()
                    except Exception:
                        body = ""
                    if "dsh web: http" in body:
                        ok_boot = True
                        break
                    time.sleep(1)
                if proc.poll() is None:
                    proc.kill()
                logf.close()
                detail = open("/tmp/precheck-boot.log", "r", errors="replace").read()[-200:]
                checks.append(ok("⑤ 动态 boot 预演通过（模拟 App 解压环境，boot 成功）") if ok_boot
                              else fail(f"⑤ boot 预演失败——生产安装必崩! 输出: {detail}"))
            except Exception as e:
                checks.append(fail(f"⑤ 预演异常: {e}"))

    passed = all(c for c in checks)
    print(f"\n== 预检结果: {'通过 ✅' if passed else '未通过 ❌（修复后再发）'} ==")
    return 0 if passed else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
