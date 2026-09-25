# -*- coding:utf-8 -*-
"""命令行装配层：参数 → 配置 → 输出根目录 → 导出引擎。

接缝说明：cli 只做装配。导入本模块无任何副作用（不配日志、不读
文件），所有装配发生在 main() 内。网页候选开关经
sources._reset_page_candidate_state 传播。
"""
import argparse
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path

from integrity import ExportMode, ManifestSchemaError
from exporter import (
    DEFAULT_OUTPUT_ROOT,
    determine_exit_code,
    export_collection_with_integrity,
    save_integrity_report,
)
from paths_config import load_config, parse_output_path
import sources


def setup_logging(logs_dir):
    """配置根日志：文件(DEBUG) + 控制台(INFO)，返回调试日志路径。"""
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log_file = logs_dir / f"debug_{timestamp}.log"

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )

    file_handler = logging.FileHandler(debug_log_file, encoding='utf-8', mode='w')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.info(f"日志系统初始化完成，日志文件: {debug_log_file}")
    return debug_log_file


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="导出知乎收藏夹并校验内容完整性")
    parser.add_argument("--audit", action="store_true", help="严格获取并审计所有支持内容，不修改文件")
    parser.add_argument("--repair", action="store_true", help="与--audit配合，修复审计发现的问题")
    parser.add_argument("--force", action="store_true", help="强制重新下载并校验所有支持内容")
    parser.add_argument(
        "--no-page-candidates",
        action="store_true",
        help="完全禁用回答网页候选抓取，仅用 API 候选校验",
    )
    parser.add_argument(
        "--page-candidates",
        action="store_true",
        help="急切抓取回答网页候选（默认仅当 API 候选失败时才尝试网页候选）",
    )
    args = parser.parse_args(argv)
    if args.no_page_candidates and args.page_candidates:
        parser.error("--page-candidates 与 --no-page-candidates 不能同时使用")
    if args.repair and not args.audit:
        parser.error("--repair 必须与 --audit 一起使用")
    if args.audit and args.force:
        parser.error("--audit 与 --force 不能同时使用")
    if args.audit and args.repair:
        args.mode = ExportMode.AUDIT_REPAIR
    elif args.audit:
        args.mode = ExportMode.AUDIT
    elif args.force:
        args.mode = ExportMode.FORCE
    else:
        args.mode = ExportMode.BALANCED
    return args


def main(argv=None):
    args = parse_args(argv)
    # 每次运行从参数重建网页候选状态（默认懒升级：API 候选失败才抓网页）
    sources._reset_page_candidate_state(
        enabled=not args.no_page_candidates,
        eager=args.page_candidates,
    )

    setup_logging(DEFAULT_OUTPUT_ROOT / "logs")
    config = load_config()

    output_root = None
    if config.get('outputPath'):
        output_root = parse_output_path(config['outputPath'], config.get('os', ''))
        if output_root:
            print(f"使用自定义输出路径: {output_root}")
            setup_logging(output_root / "logs")
        else:
            print("输出路径解析失败，使用默认路径")
    else:
        print("使用默认输出路径: downloads/")

    if config.get('openCollection', False):
        print("检测到openCollection模式已启用，请先运行 fetch_collections.py")
        return 2

    zhihu_collections = config.get('zhihuUrls', [])
    if not isinstance(zhihu_collections, list) or not zhihu_collections:
        print("没有找到要处理的收藏夹配置")
        return 2

    print(f"运行模式: {args.mode.value}")
    if args.no_page_candidates:
        logging.info("已启用 --no-page-candidates，本次运行跳过回答网页候选抓取")
    if args.page_candidates:
        logging.info("已启用 --page-candidates，本次运行将急切抓取回答网页候选")
    print(f"共找到 {len(zhihu_collections)} 个收藏夹待处理")
    collection_reports = []
    for collection in zhihu_collections:
        collection_name = collection.get('name', '未命名收藏夹')
        collection_url = collection.get('url', '')
        if not collection_url:
            collection_reports.append({
                "name": collection_name,
                "url": collection_url,
                "status": "config_error",
                "collection": {"complete": False},
                "items": [],
            })
            continue
        print(f"\n开始处理收藏夹: {collection_name}")
        try:
            report = export_collection_with_integrity(
                collection_name,
                collection_url,
                mode=args.mode,
                output_root=output_root,
            )
        except ManifestSchemaError as exc:
            report = {
                "name": collection_name,
                "url": collection_url,
                "status": "manifest_error",
                "collection": {"complete": False},
                "items": [],
                "issues": [{"code": "manifest_error", "message": str(exc)}],
            }
        except Exception as exc:
            logging.error(f"收藏夹完整性导出失败: {collection_name}: {exc}")
            logging.error(traceback.format_exc())
            report = {
                "name": collection_name,
                "url": collection_url,
                "status": "invalid",
                "collection": {"complete": True},
                "items": [],
                "issues": [{"code": "collection_exception", "message": str(exc)}],
            }
        collection_reports.append(report)

    report_path = save_integrity_report(
        collection_reports, args.mode, logs_dir=output_root / "logs" if output_root else None
    )
    exit_code = determine_exit_code(collection_reports)
    verified = sum(
        1
        for collection in collection_reports
        for item in collection.get("items", [])
        if item.get("status") in {"downloaded", "repaired", "refreshed", "adopted", "audit_verified", "skipped_verified"}
    )
    failed = sum(
        1
        for collection in collection_reports
        for item in collection.get("items", [])
        if item.get("status") in {
            "invalid", "metadata_failed", "source_failed", "render_failed",
            "render_invalid", "final_invalid"
        }
    )
    unsupported = sum(
        len(collection.get("collection", {}).get("unsupported_items", []))
        for collection in collection_reports
    )
    print("\n完整性处理完毕")
    print(f"已验证支持内容: {verified}")
    print(f"失败支持内容: {failed}")
    print(f"不支持内容: {unsupported}")
    print(f"完整性报告: {report_path}")
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
