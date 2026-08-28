#!/usr/bin/env python3
"""坏插件：初始化即抛异常 —— 用于演示闸① 验收拦截（approve 拒绝替换）。"""


def init(ctx):
    raise RuntimeError("模拟插件初始化失败：导入的依赖未就绪 / 配置缺失")


def run(ctx):
    return {"ok": True}
