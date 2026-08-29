<div align="center">

# dsh-isolation-box

**DSH 插件调试隔离框架** —— 进程隔离 · 看门狗 · 熔断 · 验收 · 启动守卫 · 发布预检

[![version](https://img.shields.io/npm/v/dsh-isolation-box/beta?style=flat-square&label=beta)](https://www.npmjs.com/package/dsh-isolation-box)
[![license](https://img.shields.io/badge/license-MIT-536990?style=flat-square)](LICENSE)
[![python](https://img.shields.io/badge/python-3.12+-4b6fff?style=flat-square)](https://www.python.org)
[![node](https://img.shields.io/badge/node-24+-7da1de?style=flat-square)](https://nodejs.org)
[![deps](https://img.shields.io/badge/deps-zero%20third--party-brightgreen?style=flat-square)](package.json)

</div>

> 在无法使用内核沙箱的受限容器环境中，用「进程边界」接住调试的崩溃，用「五道预检」拦住生产的错误，用「熔断与回滚」兜住所有的意外——**宿主永远先活着，插件永远轻装出发。**

本框架专为 [DSHA](https://github.com/qiannianhuanxiang/DSHA)（DeepSeek Harness App）适配设计。调试 DSH 插件时，最怕三件事：插件崩溃拖垮宿主、坏代码污染正式区、测试通过但安装后崩。本框架用**纯进程级隔离**（无需 namespace/root）解决这三件事：零第三方依赖，全部 Python 标准库。

## ✨ 特性

| 能力 | 说明 |
|---|---|
| 🧱 **进程级隔离** | 实例是独立进程（rlimit 硬限内存/CPU/core），崩溃信号天然不跨进程——宿主永不随插件崩 |
| ⏱️ **看门狗 + 熔断** | 2s 心跳、5s 超时进程组强杀；连崩 3 次自动熔断止损，等人工处理 |
| ✅ **验收闸 ①** | 实例冒烟测试（加载 + 初始化），通过才允许替换正式插件目录 |
| 🛡️ **启动守卫 ②③** | 启动失败自动回滚备份 → 仍失败进入安全模式（跳过全部插件），DSH 必起 |
| 📦 **发布五道预检** | 顶层名=包名 / 依赖闭包实体 / bundle 声明 / 客户端指纹 / 动态 boot 预演，全绿才可发布 |
| 📊 **浏览器看板** | 只读状态看板（`127.0.0.1:8765`），3s 自动刷新，崩溃行标红 |
| 🖥️ **完整实例层** | 拉起与主实例完全同构的独立 DSH（独立 profile/端口/进程/GUI），插件隔离测试 |
| 🔍 **崩溃取证** | stderr 尾 200 行 + 退出码 + 状态文件自动落盘，现场可回放 |
| 💾 **会话保险** | 启动自动备份；最后一步强制备份+停进程；安全重启防 #420；独立守卫自动还原 |

## 🚀 快速开始

```bash
# 1. 验收一个插件：实例冒烟通过才允许进正式目录（退出码 0=通过）
python3 supervisor.py approve ./your_plugin.py

# 2. 监督它：崩溃自动退避重启，连崩 3 次熔断
python3 supervisor.py supervise --specs demo1=./your_plugin.py

# 3. 围观：浏览器看板（3s 自动刷新，崩溃行变红）
python3 supervisor.py board
```

## 📦 安装为 DSH 插件

**方式一：npm registry**（推荐，已发布，任何人都可安装）：

```bash
# 正式版（v0.1.2，稳定）
dsh plugin --profile web add dsh-isolation-box
# 测试版（v0.1.3-beta.3，含会话保险，欢迎试用反馈）
dsh plugin --profile web add dsh-isolation-box@beta
```

**方式二：GitHub 仓库**（公开仓库，零配置；正式版 tag `v0.1.2`，测试版 tag `v0.1.3-beta.3`）：

```bash
dsh plugin --profile web add github:Xing-Hen-Hen/dsh-isolation-box#v0.1.3-beta.3
```

安装后重启 DSH Web Host 生效。插件本体是零依赖挂载点（`lib/index.js`），只打印挂载日志；工具集（`scripts/`）按需运行，**默认 0 进程静默待命**。（DSHA 用户也可在 App「插件」页直接导入发布物。）

`main` 分支为开发分支，发布物不稳定，不提供安装命令；如需最新开发版或自行修改，可 clone 仓库后本地使用。

## 🛠️ 命令速查

| 命令 | 作用 |
|---|---|
| `supervisor.py supervise --specs <id>=<plugin.py>` | 监督实例（退避重启 / 熔断 / 取证） |
| `supervisor.py approve <plugin.py>` | 闸① 验收：冒烟通过才允许替换 |
| `supervisor.py status` / `logs <id>` | 实例状态表 / 崩溃日志尾部 200 行 |
| `supervisor.py kill <id>` / `disable <id>` | 强杀进程组 / 人工禁用 |
| `supervisor.py board [--open]` | 浏览器看板（`127.0.0.1:8765`） |
| `dsh_guard.py backup` | 闸① 通过后、替换前备份正式目录 |
| `dsh_guard.py start --cmd "dsh web" --timeout 60` | 闸②③ 带回滚/安全模式的启动 |
| `dsh_tool.py up <name> [--port]` | 拉起完整 DSH 实例（同构主 profile） |
| `dsh_tool.py down <name>` / `status` | 停止实例 / 列出实例 |
| `dsh_tool.py publish --src <dir> --name <pkg> --version <v> [--fingerprint <f>]` | 发布唯一入口：五道预检全绿才产出可导入包 |
| `supervisor.py finalize --plugin <path>` | **最后一步**：强制备份会话 → 停全部实例/看板 → 询问是否安装进主进程 |
| `supervisor.py stop-all` | 停止全部实例进程 + 看板（校验 instance_runner 零残留） |
| `backup.py sessions [--reason <说明>]` | 备份会话（对话）→ 打印备份目录与还原方法 |
| `backup.py dsh` / `list` / `restore <名> [--session <id>]` / `verify <名>` | 完整备份 / 列出备份 / 还原（默认单会话，整树需确认）/ 校验 |
| `safe_restart.py [--dry-run] [--profile <名>]` | 安全重启 DSH：备份→优雅停止→等端口释放→守卫检查→拉起（`--profile` 指定实例防误杀主进程） |
| `session_guard.py` | 独立还原守卫：DSH 启动前检测会话损坏 → 自动从备份还原（不依赖 Agent） |

## 💾 会话保险（最后一步）

DSH 自身**没有会话备份能力**（只备份插件/配置清单）。本框架补上「存档 + 守卫」两层，防止装完插件重启时会话损坏（官方 #420：双进程写同一会话 → seq 重复）：

```
① 启动时        supervisor 每次调用默认自动备份会话（--no-backup 关闭）
② 最后一步      supervisor.py finalize：强制备份（含当前对话）→ 停全部实例/看板
                 → 打印备份目录与还原方法 → 询问「确认安装进主进程？」→ 确认后 safe_restart
③ 安全重启      safe_restart.py：备份 → SIGTERM 优雅停止 → 等端口彻底释放（#420 解药）
                 → session_guard 检查 → 拉起 → 等就绪
④ 自动还原      session_guard.py（独立守卫，不依赖 Agent）：启动前检测会话与备份不一致
                 → 自动从备份还原 + 损坏文件留证
```

- **备份目录**：`$DSH_HOME/backups/<类型>-<时间戳>/`（每类保留最近 5 份）
- **还原**：`backup.py restore <备份名> --session <会话id>` 单会话恢复（推荐）；整树还原会回退全部会话，需二次确认；dsh 运行中还原会被拒绝（`--force` 可强）
- **诚实边界**：自动还原仅在重启走 `safe_restart.py`（或 App/watchdog 正常重启）时生效；**纯手动强杀+强拉没有守卫**，但备份仍在，一条命令手动还原

## 📖 插件契约（开发者）

**契约 = 隔离箱与待测插件之间的接口约定**：隔离箱负责把插件加载进独立进程、注入上下文 `ctx`、跑心跳/冒烟/取证；插件只需按约定实现下面两个**可选**函数，即可被隔离箱驱动：

```python
def init(ctx) -> None      # 可选：初始化（冒烟测试会执行到这里）
def run(ctx) -> dict       # 可选：主逻辑，返回结果字典
# ctx.work_dir 实例工作区; ctx.task 宿主注入的任务
# ctx.report(status, detail); ctx.log(msg)
```

- 两个函数都可不实现（隔离箱仍能监督实例、看心跳）
- `init` 抛异常 = 冒烟失败 = 验收闸① 拦截，禁止进正式目录
- `run` 的返回值会落盘到 `result.json` 供宿主读取

## 🔍 工作原理

崩溃本质是**进程属性**而非环境属性：不需要 namespace，只要实例是独立进程，信号传播天然不跨进程。配套三类实测可用的原语：

- **rlimit 硬上限** —— 内存泄漏 / 死循环 / fork 炸弹被约束在实例内
- **心跳 + 看门狗** —— 挂死由宿主定时器进程组 SIGKILL 回收
- **指数退避重启 + 熔断** —— 崩了自动试，连崩 3 次止损等人工

环境等价性（`scripts/equiv_probe.py` 实测）：实例与宿主共享同一运行时 / 依赖面 / 工具链，试跑结果可信。

## 📁 文档

| 文档 | 内容 |
|---|---|
| [SAFETY.md](SAFETY.md) | 调试安全纪律：触发纪律 / 绝对不动清单 / 三档恢复 |
| [docs/PRINCIPLE.md](docs/PRINCIPLE.md) | 原理说明：为什么进程级隔离成立、诚实边界 |
| [docs/multi-instance-plugin-design.md](docs/multi-instance-plugin-design.md) | 完整设计文档：环境体检、架构、防御矩阵、生命周期 |

## ⚙️ 环境要求

- **操作系统**：Linux（aarch64 / x86_64），已在 Ubuntu 24.04 aarch64 验证
- **Python**：3.12+（标准库，零第三方依赖）
- **Node.js**：24+（`dsh_tool.py` 完整 DSH 实例层使用）
- **DSHA 环境**：提供 DSH 桥接通道（`/app/*`），用于自动打开浏览器/看板；缺失时自动降级为打印访问地址

路径均可用环境变量覆盖（`DSH_HOME` / `DSH_INSTANCES_ROOT` / `DSH_EXPORT_DIR` 等），解压到任意目录即可运行。

## 🧪 演示与验证

`scripts/demo_story.py` 四幕全链路自动化验证：

1. **崩溃自愈**：坏插件连崩 2 次 → 第 3 次自动成功（宿主零影响）
2. **熔断**：永远坏的插件连崩 3 次 → 熔断停在「需人工」
3. **验收拦截**：坏插件冒烟失败禁止替换；好插件放行
4. **启动守卫**：启动失败 → 自动回滚重试成功；回滚也失败 → 安全模式必起

## ⚠️ 诚实边界

- **防 bug，不防恶意**：**可以测试陌生人代码**——实例是独立进程，其崩溃/死循环不会拖垮宿主；但实例与宿主同 uid、无权限隔离，恶意代码理论上可越界读写全盘，测不可信代码时请自行评估风险
- **无网络隔离 / 文件系统不虚拟化**：依赖「约定 + 独立状态目录」，不是硬墙
- 详细边界见 [docs/PRINCIPLE.md](docs/PRINCIPLE.md) 第六节

## 📄 许可证

[MIT](LICENSE)
