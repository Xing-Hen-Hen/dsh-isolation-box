# 调试安全守则（SAFETY.md）——「不伤到宿主」的操作纪律

> 背景：宿主（DSH 主实例/我的会话）与调试对象（实例/测试 profile）共享同一文件系统与 uid。
> 本守则把「不会输错命令伤到自己」从承诺变成**可执行的纪律**。

## 零、触发纪律（防止「闲着没事启动隔离箱」）

| 场景 | 隔离箱行为 |
|---|---|
| **改插件 / 调整程序 / 修代码**（调试、试跑、发布、预检） | ✅ **正常触发**（这是它的本职） |
| **其他一切情况**（日常任务、查状态、普通对话） | 🚫 **不触发**——实例不拉起、看板不启动、dsh_tool 不运行 |
| **用户直接说「用隔离箱 / 触发隔离箱」** | ✅ 按需触发 |
| 触发后的自动行为 | 仅限发布预检（publish 五道）等「该动作内嵌」的检查；不得擅自拉起额外实例/服务 |

**默认态**：隔离箱全体静默待命（0 进程），仅在使用场景 + 必要性驱动时启动。

## 零·一、必须执行（硬性规则，不可跳过）

| # | 规则 | 说明 |
|---|---|---|
| 1 | **会话备份必须默认保存到 `/sdcard/Download/DSHA/<当前会话编号>/` 下的两个子文件夹** | 隔离箱启动自动备份（**完整备份**：会话+配置+插件清单）→ `auto-backups/`；日志备份（backup.py 显式/finalize/safe_restart）→ `session-logs/`；父目录可用 `DSH_BACKUP_ROOT` 覆盖 |
| 1a | **两类备份发生时都必须告知用户** | 每次备份必须打印备份目录路径与还原方法（`[backup] ✅ 会话已备份 → ...`）|
| 2 | 每次备份后必须确认 `✅ 会话已备份` 输出与备份目录生成 | 未确认不进入下一步（调试/替换/重启） |
| 3 | 最后一步（finalize）询问前必须已备份（含当前对话）并打印备份目录 | 备份未完成不得询问「是否安装进主进程」 |
| 4 | restore 默认用 `--session <id>` 单会话恢复；整树还原必须确认 | 上次事故教训：整树还原回退全部会话 |
| 5 | dsh 运行中禁止 restore（除非 `--force`） | 上次事故教训：运行中还原造成会话索引错乱 |

## 一、绝对不动清单（调试期间永不触碰）

| 对象 | 位置 | 原因 |
|---|---|---|
| 主 DSH 进程 | `node lib/bin.js web`（PID 见 ps, 非 `--profile test-*` 的） | 杀它 = 用户 GUI 消失 |
| 主 profile | `~/.dsh/profiles/web/`（只读参考） | 用户实例的根,只 allow 只读 |
| 用户层配置 | `~/.dsh/cordis.patch.yml`（备份在 `~/.dsh/backups/`） | 改它会影响主实例行为 |
| 会话库 | `~/.dsh/sessions/` | 用户历史对话 |
| 设备系统侧 | 一律走 App 层接口或 adb-shell（用户确认） | 越过 = 违规 |

## 二、危险操作规范（每一条都是「防自杀」）

1. **杀进程**：先 `pgrep -f` 打印**完整清单**并人工核对 → 必须用带正则防自匹配的 pattern（如 `pgrep -f "lib/bin[.]js --profile test-2"`）→ 确认 pid 后再 kill。**禁止**裸 `pkill -f xxx`（曾 2 次自杀,血泪）
2. **删除/移动**：路径限定在 `~/project/plugins-framework/`、`~/project/instances/`、`$DSH_HOME/profiles/test-*` 内；删前 `.git status` 或 `ls` 确认
3. **修改宿主文件**：先 `cp` 到 `$DSH_HOME/backups/` 再改
4. **重启生效类**：只有「验收通过（approve 冒烟 + 一致性）」才允许替换=重启 = 走 `dsh_tool.py` / `dsh_guard.py`,不裸跑
5. **设备命令**：先报备,走确认通道,不绕过

## 三、出错后的恢复（三档）

| 档 | 手段 | 命令 |
|---|---|---|
| 框架文件改坏 | git 回滚 | `cd ~/project/plugins-framework && git checkout -- .` |
| 测试实例搞坏 | 重建实例 | `dsh_tool.py down x && dsh_tool.py up x --port 3083` |
| 宿主配置改坏 | 备份恢复 | `cp $DSH_HOME/backups/cordis.patch.yml.bak-* $DSH_HOME/cordis.patch.yml` |

## 四、验证过的「安全圈」结构

```
可自由操作区（调试战场）:
  ~/project/plugins-framework/   ← git 仓库,任何破坏可回滚
  ~/project/instances/            ← 实例数据,可整体重建
  $DSH_HOME/profiles/test-*/             ← 测试 profile,可重建
  $DSH_HOME/profiles/web/node_modules/.ignored_*  ← 只读参考源
不可碰区（宿主）:
  主进程 / web profile / cordis.patch.yml / sessions
```
