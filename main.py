# -*- coding:utf-8 -*-
import argparse
import os
from datetime import datetime
from utils import filter_title_str
from paths_config import load_config, parse_output_path
from integrity import ExportMode, ManifestSchemaError
from exporter import determine_exit_code, export_collection_with_integrity, save_integrity_report
from sources import get_article_urls_in_collection
import logging
import traceback
import pathlib



# 读取配置文件
def get_current_os():
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "darwin":
        return "macos"
    elif system == "linux":
        return "linux"
    else:
        return "unknown"

# 解析路径，根据操作系统类型处理
# 全局配置和路径管理
config = {}
base_output_path = None

# 设置调试日志
def setup_debug_logging():
    # 初始化时使用默认路径，稍后会在main中重新配置
    logs_dir = os.path.join(os.path.dirname(__file__), 'downloads', 'logs')
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log_file = os.path.join(logs_dir, f"debug_{timestamp}.log")
    
    # 清除所有已存在的处理器
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 创建文件处理器，立即写入
    file_handler = logging.FileHandler(debug_log_file, encoding='utf-8', mode='w')
    file_handler.setLevel(logging.DEBUG)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # 设置格式
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # 配置根日志记录器
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # 测试日志写入
    logging.info(f"日志系统初始化完成，日志文件: {debug_log_file}")
    
    # 强制刷新
    for handler in root_logger.handlers:
        if hasattr(handler, 'flush'):
            handler.flush()
    
    return debug_log_file

# 重新配置日志路径
def reconfigure_logging():
    logs_dir = get_logs_path()
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log_file = os.path.join(logs_dir, f"debug_{timestamp}.log")
    
    # 清除已有的handler
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.flush()  # 确保刷新
        root_logger.removeHandler(handler)
    
    # 创建新的处理器
    file_handler = logging.FileHandler(debug_log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # 设置格式
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # 重新添加处理器
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.DEBUG)
    
    return debug_log_file

# 获取输出路径的函数
def get_output_path(collection_name):
    """
    根据配置获取输出路径
    如果配置了outputPath，使用自定义路径
    否则使用默认的downloads路径
    """
    global base_output_path

    # 收藏夹名可能含路径分隔符等非法字符（如 "C/C++"），清理为安全的目录名
    safe_name = filter_title_str(collection_name).strip()
    if not safe_name:
        safe_name = "未命名收藏夹"

    if base_output_path:
        # 使用自定义输出路径
        return os.path.join(str(base_output_path), safe_name)
    else:
        # 使用默认路径
        return os.path.join(os.path.dirname(__file__), 'downloads', safe_name)

def get_logs_path():
    """
    获取日志路径
    """
    global base_output_path
    
    if base_output_path:
        # 使用自定义输出路径
        return os.path.join(str(base_output_path), 'logs')
    else:
        # 使用默认路径
        return os.path.join(os.path.dirname(__file__), 'downloads', 'logs')

def get_debug_path():
    """
    获取调试文件路径
    """
    global base_output_path
    
    if base_output_path:
        # 使用自定义输出路径
        return os.path.join(str(base_output_path), 'debug')
    else:
        # 使用默认路径
        return os.path.join(os.path.dirname(__file__), 'downloads', 'debug')

debug_log_file = setup_debug_logging()


def process_single_collection(collection_name, collection_url):
    """Compatibility entry point used by the MCP server."""
    return export_collection_with_integrity(
        collection_name,
        collection_url,
        mode=ExportMode.BALANCED,
    )


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
    global config, base_output_path
    global PAGE_CANDIDATES_ENABLED, PAGE_CANDIDATES_EAGER
    args = parse_args(argv)
    if args.no_page_candidates:
        PAGE_CANDIDATES_ENABLED = False
    if args.page_candidates:
        PAGE_CANDIDATES_EAGER = True
    config = load_config()

    if config.get('outputPath'):
        base_output_path = parse_output_path(config['outputPath'], config.get('os', ''))
        if base_output_path:
            print(f"使用自定义输出路径: {base_output_path}")
            reconfigure_logging()
        else:
            print("输出路径解析失败，使用默认路径")
            base_output_path = None
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
                output_root=base_output_path,
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

    report_path = save_integrity_report(collection_reports, args.mode)
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

# def testMarkdownifySingleAnswer():
#     url = "https://www.zhihu.com/question/506166712/answer/2271842801"
