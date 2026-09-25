# -*- coding:utf-8 -*-
"""向后兼容入口。

主逻辑已拆分为深模块：
- paths_config  配置 / 跨平台路径 / Cookie
- zhihu_client  知乎 HTTP 边界适配器（传输 + 头部 + 重试）
- sources       知乎源数据抓取（分页对账 / 双候选 / 熔断）
- render        快照 HTML → Markdown 渲染 + 图片资产
- integrity     完整性校验核心（纯逻辑）
- exporter      导出引擎（清单 / 报告 / 退出码）
- cli           命令行装配（无导入副作用）

命令行用法保持不变：python main.py [--audit|--force|...]
"""
from cli import main, parse_args, setup_logging  # noqa: F401  兼容旧导入路径

from paths_config import load_config, parse_output_path  # noqa: F401  MCP 兼容
from sources import get_article_urls_in_collection  # noqa: F401  MCP 兼容
from exporter import DEFAULT_OUTPUT_ROOT, export_collection_with_integrity  # noqa: F401
from utils import filter_title_str  # noqa: F401
import os  # noqa: F401


def get_output_path(collection_name):
    """兼容旧调用方的输出路径解析（MCP server）；新代码请用
    exporter.collection_output_dir(output_root, name)。"""
    from exporter import collection_output_dir
    return str(collection_output_dir(DEFAULT_OUTPUT_ROOT, collection_name))


def process_single_collection(collection_name, collection_url):
    """Compatibility entry point used by the MCP server."""
    return export_collection_with_integrity(
        collection_name,
        collection_url,
    )


if __name__ == '__main__':
    raise SystemExit(main())
