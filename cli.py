# -*- coding:utf-8 -*-
"""命令行装配层：参数 → 配置 → 输出根目录 → 导出引擎。

接缝说明：cli 只做装配。导入本模块无任何副作用（不配日志、不读
文件），所有装配发生在 main() 内。网页候选开关经
sources._reset_page_candidate_state 传播。
"""
import argparse
import logging
import os
import time
import traceback
from datetime import datetime
from pathlib import Path

from integrity import ExportMode, ManifestSchemaError
from exporter import (
    DEFAULT_OUTPUT_ROOT,
    determine_exit_code,
    export_collection_with_integrity,
    save_integrity_report,
    summarize_collection_reports,
)
from paths_config import (
    COLLECTION_ARCHIVED_STATUS,
    load_config,
    mark_collections_archived,
    parse_output_path,
)
import sources
from zhihu_client import (
    AuthenticationCheckError,
    AuthenticationError,
    preflight_authentication,
)


def setup_logging(logs_dir):
    """配置根日志：文件(DEBUG) + 控制台(INFO)，返回调试日志路径。"""
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log_file = logs_dir / f"debug_{timestamp}.log"

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

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
    started_at = time.monotonic()
    args = parse_args(argv)
    # 每次运行从参数重建网页候选状态（默认懒升级：API 候选失败才抓网页）
    sources._reset_page_candidate_state(
        enabled=not args.no_page_candidates,
        eager=args.page_candidates,
    )

    config = load_config()

    output_root = None
    if config.get('outputPath'):
        output_root = parse_output_path(config['outputPath'], config.get('os', ''))
        if output_root:
            print(f"使用自定义输出路径: {output_root}")
        else:
            print("输出路径解析失败，使用默认路径")
    else:
        print(f"使用默认输出路径: {DEFAULT_OUTPUT_ROOT}")
    effective_output_root = output_root or DEFAULT_OUTPUT_ROOT
    setup_logging(effective_output_root / "logs")

    if config.get('openCollection', False):
        print("检测到openCollection模式已启用，请先运行 fetch_collections.py")
        return 2

    zhihu_collections = config.get('zhihuUrls', [])
    if not isinstance(zhihu_collections, list) or not zhihu_collections:
        print("没有找到要处理的收藏夹配置")
        return 2

    try:
        auth_status = preflight_authentication()
    except (AuthenticationError, AuthenticationCheckError) as exc:
        logging.error(f"认证预检失败，已停止导出: {exc}")
        print(f"认证预检失败: {exc}")
        report_path = save_integrity_report(
            [{
                "name": "认证预检",
                "url": "",
                "status": "authentication_failed",
                "collection": {"complete": True},
                "items": [],
                "issues": [{"code": "authentication_failed", "message": str(exc)}],
            }],
            args.mode,
            logs_dir=effective_output_root / "logs",
        )
        print(f"完整性报告: {report_path}")
        return 3
    if auth_status.authenticated:
        logging.info("Cookie 认证预检通过")
    else:
        logging.info("未配置 Cookie，使用匿名模式；远端删除仅报告，不归档")

    print(f"运行模式: {args.mode.value}")
    if args.no_page_candidates:
        logging.info("已启用 --no-page-candidates，本次运行跳过回答网页候选抓取")
    if args.page_candidates:
        logging.info("已启用 --page-candidates，本次运行将急切抓取回答网页候选")
    print(f"共找到 {len(zhihu_collections)} 个收藏夹待处理")
    collection_reports = []
    skipped_archived = 0
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
        if collection.get('status') == COLLECTION_ARCHIVED_STATUS:
            # config 已标记远端删除：跳过请求（404 无需重复确认）。
            # 重跑 fetch_collections.py 重建列表可清除标记，手动删除字段亦可。
            skipped_archived += 1
            logging.info(f"跳过已归档收藏夹: {collection_name}")
            collection_reports.append({
                "name": collection_name,
                "url": collection_url,
                "status": "archived_skipped",
                "collection": {"complete": True},
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
                allow_deletion_sync=auth_status.authenticated,
            )
        except (AuthenticationError, AuthenticationCheckError) as exc:
            logging.error(f"认证状态失效，停止剩余收藏夹: {exc}")
            report = getattr(exc, "collection_report", None) or {
                "name": collection_name,
                "url": collection_url,
                "collection": {"complete": True},
                "items": [],
            }
            report["status"] = "authentication_failed"
            report.setdefault("issues", []).append({
                "code": "authentication_failed", "message": str(exc)
            })
            collection_reports.append(report)
            break
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

    newly_archived_urls = [
        report.get("url")
        for report in collection_reports
        if report.get("status") == "collection_archived"
        and report.get("url")
        and not report.get("audit_only")
    ]
    if newly_archived_urls:
        # 把本轮检测到的远端删除写回 config（audit_only 报告已排除）
        try:
            mark_collections_archived('config.json', newly_archived_urls)
        except (OSError, ValueError) as exc:
            logging.warning(f"回写已归档收藏夹标记到 config.json 失败: {exc}")

    report_path = save_integrity_report(
        collection_reports, args.mode, logs_dir=effective_output_root / "logs"
    )
    exit_code = determine_exit_code(collection_reports)
    summary = summarize_collection_reports(collection_reports)
    images = summary["images"]
    print("\n完整性处理完毕")
    print(f"处理收藏夹: {summary['processed_collections']}")
    print(f"已验证支持内容: {summary['verified']}")
    print(
        f"本轮变更: 新下载 {summary['downloaded']}，修复 {summary['repaired']}，"
        f"刷新 {summary['refreshed']}，未变化 {summary['unchanged']}"
    )
    print(f"失败支持内容: {summary['failed']}")
    print(f"不支持内容: {summary['unsupported']}")
    if summary["archived_collections"]:
        print(f"已归档收藏夹（远端已删除）: {summary['archived_collections']}")
    if skipped_archived:
        print(f"跳过已归档收藏夹（config 已标记）: {skipped_archived}")
    if summary["archived_items"]:
        print(f"已归档移出条目: {summary['archived_items']}")
    if summary["archivable_items"]:
        print(f"仅报告未归档条目: {summary['archivable_items']}")
    print(
        f"图片: 新下载 {images['downloaded']}，复用 {images['reused']}，"
        f"本地完整 {images['verified']}，远程降级 {images['remote_fallback']}，"
        f"缺失 {images['missing']}"
    )
    print(f"输出目录: {effective_output_root}")
    print(f"耗时: {time.monotonic() - started_at:.1f} 秒")
    print(f"完整性报告: {report_path}")
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
