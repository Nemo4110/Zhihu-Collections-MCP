# -*- coding:utf-8 -*-
"""命令行入口（向后兼容保留；装配逻辑见 cli.py）。

主逻辑已拆分为深模块：
- paths_config  配置 / 跨平台路径 / Cookie
- zhihu_client  知乎 HTTP 边界适配器（传输 + 头部 + 重试）
- sources       知乎源数据抓取（分页对账 / 双候选 / 熔断）
- render        快照 HTML → Markdown 渲染 + 图片资产
- integrity     完整性校验核心（纯逻辑）
- exporter      导出引擎（清单 / 报告 / 退出码）
- cli           命令行装配（无导入副作用）

用法不变：python main.py [--audit|--force|--no-page-candidates|...]
"""
from cli import main

if __name__ == '__main__':
    raise SystemExit(main())
