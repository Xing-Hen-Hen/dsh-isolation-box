/**
 * dsh-isolation-box —— 隔离箱插件挂载点
 *
 * 隔离箱本体是 scripts/ 下的 Python 工具集（独立进程运行，零 Node 依赖）：
 *   supervisor.py      进程级隔离/看门狗/熔断/取证（试跑宿主）
 *   instance_runner.py 实例骨架（心跳/契约/冒烟）
 *   dsh_guard.py       启动守卫（备份/回滚/安全模式）
 *   dsh_tool.py        完整 DSH 实例管理与发布唯一入口（publish 五道预检）
 *   release_check.py   插件发布预检（格式/依赖/bundle/指纹/动态boot预演）
 *   其它：演示与示例插件（demo_story/plugin_demo/task_plugin/bad_plugin/…）
 *
 * 本文件只做「挂载 + 探测」：不 import 任何外部包（零依赖），apply 全兜底，
 * 保证 loader 挂载零风险（不会因本插件让 DSH boot 失败）。
 *
 * @module dsh-isolation-box
 */
export const name = 'dsh-isolation-box';

export function apply(ctx) {
  try {
    // 直接 console.log（写入 boot stdout，可观察）；ctx.logger 仅作补充
    console.log('[isolation-box] 隔离箱已挂载：工具集见 scripts/（supervisor / dsh_tool / release_check 等），文档见 README.md / SAFETY.md');
    if (typeof ctx?.logger?.info === 'function') {
      ctx.logger.info('[isolation-box] 已挂载（logger 侧记录）');
    }
  } catch (_) {
    // 挂载点只做探测日志；任何异常都不影响 boot
  }
}
