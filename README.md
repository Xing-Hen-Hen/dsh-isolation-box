# DSH 多实例插件框架 · 使用手册

> 零第三方依赖 · 全部标准库 · 已在 Ubuntu 24.04 (aarch64) / Python 3.12+ / Node 24+ 验证
> 设计文档：`docs/multi-instance-plugin-design.md`

## 一、组成

| 文件 | 角色 |
|---|---|
| `supervisor.py` | 宿主：spawn / 心跳看门狗 / 进程组强杀 / 退避重启 / 熔断 / 取证 / 浏览器看板 |
| `instance_runner.py` | 实例骨架：心跳上报 / 状态机 / 插件契约执行 / `--smoke` 冒烟模式 |
| `dsh_guard.py` | 启动守卫：备份 / 启动失败自动回滚 / 安全模式兜底 |
| `plugin_demo.py` | 演示插件（含契约范例：init/run） |
| `bad_plugin.py` | 坏插件（演示验收拦截） |
| `dsh_sim.py` | 模拟 DSH 启动器（演示守卫，不碰真实 DSH） |
| `demo_story.py` | 四幕自动化演示（崩溃自愈/熔断/验收拦截/启动守卫） |

## 二、插件契约（写插件照这个来）

```python
def init(ctx): ...          # 可选：初始化（冒烟测试会执行到这里）
def run(ctx) -> dict: ...   # 可选：主逻辑，返回结果字典
# ctx:  ctx.work_dir 实例工作区; ctx.task 宿主注入的任务; ctx.report(status, detail); ctx.log(msg)
```

## 三、命令速查

```bash
# 监督一个/多个实例（崩了自动退避重启，连崩 3 次熔断）
python3 supervisor.py supervise --specs demo1=plugin_demo.py

# 闸① 验收：实例环境冒烟测试，通过才允许替换正式插件目录
python3 supervisor.py approve <待验收插件.py>        # 退出码 0=通过

# 浏览器看板（移动端浏览器 http://127.0.0.1:8765/，可悬浮小窗）
python3 supervisor.py board

# 查看状态 / 崩溃日志 / 强杀 / 人工禁用
python3 supervisor.py status
python3 supervisor.py logs <id>
python3 supervisor.py kill <id>
python3 supervisor.py disable <id>

# 启动守卫：闸② 备份 + 闸③ 回滚/安全模式
python3 dsh_guard.py backup                          # 通过验收后、替换前先备份
python3 dsh_guard.py start --cmd "dsh web" --timeout 60   # 真实 DSH 接入时用
```

## 四、真实 DSH 接入（待做）

`dsh_guard start --cmd` 的**就绪判定**目前以 READY 文件模拟；接入真实 `dsh web` 时：
1. 把就绪判定换成实际信号（Web GUI 端口探测 / 启动日志关键字），在 `dsh_guard.py` 的 `wait_ready()` 中适配；
2. `--cmd "dsh web"` 即为真实启动命令，回滚/安全模式逻辑不变（安全模式注入 `DSH_PLUGIN_SAFE=1`，需 DSH 侧识别该变量时跳过用户插件加载——留待 DSH 插件加载器集成时实现）。

## 五、默认参数（已确认）

内存 512MB/实例 · CPU 60s · 心跳 2s · 看门狗 5s · 退避 0.5/1/2/4s · 连崩 3 次熔断 · 看板 127.0.0.1:8765 · 3s 自动刷新

## 七、完整 DSH 实例（dsh-tool，效果层测试）

「两者结合」的第二层：每个测试实例 = **完整 DSH（独立 profile + 独立端口 + 独立进程 + 完整 GUI）**，浏览器直达，界面与主实例完全一致（含移动导航/客户端插件），插件/会话可隔离测试。

```bash
python3 dsh_tool.py up <name> --port 3082     # 拉起完整实例（自动同构主 profile，含用户插件栈）
python3 dsh_tool.py down <name>               # 停止实例
python3 dsh_tool.py status                    # 列出实例（端口/PID/访问URL）
```

访问 URL 已自动附鉴权通行证（对 web 服务的 token 鉴权，浏览器直达既安全又免配置）。

**已验证**：test-live(3081)/test-2(3082) 并行运行，HTML + `/plugins/` 客户端插件均 200，界面与主实例一致。

**踩坑记录**（已解决）：
- 裁剪版 profile 会缺 `dsh-client-*` 客户端插件路由（/plugins/ 404）与移动导航——必须与主 profile 完全同构（复制 package.json + 以正式名软链 `.ignored_*` 隐藏用户插件）
- web 响应必须带防缓存头（用户端会缓存旧快照，出现「幽灵实例」）

## 七·五、插件适配案例：dsh-reasoning-effort v0.6.2（已完成 ✔）

流程：源码进隔离箱 → 尝试源码构建 → 官方产物部署 → 测试实例验证 → **主实例零接触**。

| 环节 | 结果 |
|---|---|
| 源码管理 | 进隔离箱 git（`plugins-under-test/dsh-reasoning-effort/`，可回滚） |
| 源码构建 | ⚠️ 环境不可复现：`@deepseek-ai/cordis@4.0.1` 类型缺 `Context` 导出 + pnpm store `.ts` 分片解析失败（上游在含 workspace 内部 cordis 的环境构建） |
| 部署物 | 官方 0.6.2 发布产物（与源 md5 `0da2e38...` 一致，无污染） |
| 实例验证 | test-live 插件 client.js 200（1.77MB）、页面注入、用户确认 UI 正常 ✅ |
| 主实例 | 0 处插件痕迹，完全隔离 ✅ |

**使用**：`dsh_tool.py up test-live --port 3081` → 浏览器打开即见插件；主实例想装时按官方 README 走 `dsh plugin --profile web add ...`（需用户确认 + 手动重启主 DSH）。

## 七·八、触发纪律与发布管道（2026-08-27 定稿）

**触发纪律**（SAFETY.md 第 0 节）：
- 改插件/调整程序/修代码 → 触发隔离箱（试跑/验证/发布预检）
- 其他日常 → **不触发**（零进程静默，看板待命不弹窗）
- 用户明说「用隔离箱」→ 按需触发

**发布管道（唯一入口）**：
```bash
python3 dsh_tool.py publish --src <插件源码目录> --name <包名> --version <版本> \
       --fingerprint "<客户端产物指纹>"
# 内部强制五道预检：①顶层名=包名 ②依赖闭包实体 ③bundle声明 ④产物指纹 ⑤动态boot预演（App解压语义模拟）
# 五道全绿 → 产出 tar.gz 可导入；任何一道不过 → 禁发
```

**浏览器行为**：
- 看板（board）：**默认不弹浏览器**（`--open` 显式开启）；静默待命
- 完整实例（`dsh_tool up`）：**就绪自动弹出浏览器**（`--no-open` 可关）

## 八、演示结果（今日实测）

四幕全部通过：
1. 崩溃自愈：坏插件连崩 2 次 → 第 3 次自动成功（实例重启 2 次，宿主零影响）
2. 熔断：永远坏的插件连崩 3 次 → 熔断停在「需人工」
3. 验收拦截：坏插件冒烟失败禁止替换；好插件放行
4. 启动守卫：启动失败 → 自动回滚重试成功；回滚也失败 → 安全模式必起

期间还抓到并修复了 2 个真实 bug（线程写竞态、CSS 花括号格式化），充分说明「崩溃现场取证 → 修复 → 重跑」链路好用。

## 九、环境要求

- **操作系统**：Linux（aarch64 / x86_64），已在 Ubuntu 24.04 aarch64 验证
- **Python**：3.12+（标准库，零第三方依赖）
- **Node.js**：24+（`dsh_tool.py` 完整 DSH 实例层使用）
- **可选**：DSH 桥接通道（`/app/*`）用于自动打开浏览器/看板；缺失时工具自动降级为打印访问地址，功能不受影响
