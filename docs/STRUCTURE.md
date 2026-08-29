# dsh-isolation-box 结构说明

> 本文件说明程序包的组织结构：目录布局、每个脚本的职责、备份目录结构、整体架构关系。

## 一、包目录结构

```
dsh-isolation-box/
├── lib/
│   └── index.js              ← 插件挂载点（零依赖，仅打印挂载日志）
├── scripts/                  ← 核心工具集（Python 标准库，零第三方依赖）
│   ├── backup.py             ← 备份/还原（会话备份、程序级完整备份、md5 校验）
│   ├── session_guard.py      ← 会话还原守卫（启动前自动检测+还原，独立于 DSH）
│   ├── safe_restart.py       ← 安全停止（关闭前存档/优雅停/防双写，重启交正常流程）
│   ├── supervisor.py         ← 宿主监督者（spawn/看门狗/熔断/验收/看板/finalize）
│   ├── instance_runner.py    ← 实例骨架（心跳/状态机/插件契约执行/冒烟）
│   ├── dsh_guard.py          ← 启动守卫（备份/回滚/安全模式）
│   ├── dsh_tool.py           ← 完整 DSH 实例管理 + 发布预检入口
│   ├── release_check.py      ← 发布五道预检（依赖闭包/bundle/指纹/boot 预演）
│   ├── demo_story.py         ← 四幕自动化演示（自愈/熔断/验收/守卫）
│   └── ...（演示与示例插件）
├── docs/
│   ├── STRUCTURE.md          ← 本文件（结构说明）
│   ├── PRINCIPLE.md          ← 原理说明与诚实边界
│   └── multi-instance-plugin-design.md ← 完整设计文档
├── README.md                 ← 使用手册（特性/安装/命令/契约/原理/环境）
├── SAFETY.md                 ← 安全纪律（触发/必须执行/绝对不动/恢复）
├── LICENSE                   ← MIT
└── cordis.patch.yml          ← DSH bundle 补丁声明
```

## 二、脚本职责表

| 脚本 | 职责 | 关键命令 |
|---|---|---|
| `backup.py` | 备份/还原/校验 | `sessions`（会话）/ `dsh`（数据）/ `full`（程序级 tar）/ `list` / `verify` / `restore` |
| `session_guard.py` | 会话自动还原守卫（独立于 DSH 运行） | 读还原标记 → 对比 → 损坏自动还原 |
| `safe_restart.py` | 安全停止（关闭前安全） | 备份 → 优雅停止 → 等端口 → 写标记 → 提示重启 |
| `supervisor.py` | 宿主监督 + 最后一步 | `supervise` / `approve` / `status` / `board` / `stop-all` / `finalize` |
| `instance_runner.py` | 实例执行骨架 | 心跳上报 / 契约 init+run / `--smoke` 冒烟 |
| `dsh_guard.py` | 启动守卫 | `backup` / `start`（回滚+安全模式） |
| `dsh_tool.py` | 完整实例 + 发布 | `up` / `down` / `status` / `publish` |
| `release_check.py` | 发布五道预检 | `--tarball` 校验 |

## 三、备份目录结构（DSHA 下载目录下）

```
/sdcard/Download/DSHA/
└── <当前会话编号>/            ← 外层（按 DSH_SESSION_ID，恢复不受影响）
    ├── auto-backups/          ← 自动备份（程序级完整 .dsh 打包 tar.gz；README.txt 说明）
    ├── session-logs/          ← 日志备份（会话；README.txt 说明）
    ├── tools/                 ← 恢复工具独立副本（backup.py + session_guard.py）
    └── 恢复工具使用手册.md     ← 跟随自动备份生成
```

| 备份类型 | 内容 | 触发 | 恢复方式 |
|---|---|---|---|
| `auto-backups/dsh-full-*` | 整个 `.dsh`（程序/插件/配置） | 隔离箱触发时自动（supervisor） | 解压 tar.gz 覆盖 DSH_HOME |
| `session-logs/sessions-*` | 会话（对话） | backup.py 显式 / finalize / safe_restart | `backup.py restore --session` |

## 四、架构关系（数据流）

```
supervisor.py（宿主）──spawn──▶ instance_runner.py（实例，rlimit+心跳）
      │                              │
      │ 自动备份（auto_backup）       │ 崩溃 → 取证（stderr/state.json）
      ▼                              ▼
backup.py ──写入──▶ auto-backups/（程序级）  session-logs/（会话）
      │
      │ restore（单会话/整树 + md5 校验）
      ▼
会话真实位置（sessions，软链解析）
      ▲
      │ 损坏时
session_guard.py（独立守卫：还原标记 → 对比 → 自动还原）
      ▲
      │ 安全停止前写标记
safe_restart.py（备份 → 优雅停 → 等端口 → 写标记 → 提示重启）
```

**一句话**：supervisor 驱动调试与自动备份；backup.py 负责存档与还原（md5 验证）；safe_restart 保证关闭前安全；session_guard 在启动前兜底还原——四者构成「调试 → 存档 → 安全关闭 → 自动救回」闭环。
