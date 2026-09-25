# -*- coding:utf-8 -*-
"""导出引擎：项级 verify/adopt/repair/refresh + 完整性清单 + 运行报告。

接缝说明：给定收藏夹与输出根目录，产出 Markdown 文件、assets、
.zhihu-integrity.json 与逐项报告。网络经注入的 fetch/render 函数，
磁盘位置由 output_root 参数传入。
"""
import logging
import os
import pathlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from integrity import (
    ExportMode,
    IntegrityAction,
    IntegrityManifestStore,
    ManifestSchemaError,
    SourceContentError,
    SourceMetadata,
    atomic_write_json,
    atomic_write_text,
    decide_action,
    local_record_is_intact,
    record_from_validation,
    RunReport,
    validate_markdown,
)
from render import prefetch_images, render_markdown
from sources import (
    fetch_answer_metadata,
    fetch_article_metadata,
    fetch_collection_items,
    fetch_source_snapshot,
)
from utils import filter_title_str

# 清单写入/保存互斥锁：项级并发导出时保护 manifest.records 与 save() 序列化
_manifest_lock = threading.Lock()
# 项级并发数：控制正文 API 与渲染的并发峰值，避免触发知乎限流
ITEM_EXPORT_WORKERS = 3

DEFAULT_OUTPUT_ROOT = pathlib.Path(__file__).resolve().parent / "downloads"


def collection_output_dir(output_root, collection_name):
    """收藏夹名清理为安全的单段目录名，拼接到输出根目录下。"""
    safe_name = filter_title_str(collection_name).strip()
    if not safe_name:
        safe_name = "未命名收藏夹"
    return pathlib.Path(output_root) / safe_name


def is_article_already_downloaded(file_path, target_url):
    """
    检查文件是否已存在且包含相同的URL
    :param file_path: 要检查的markdown文件路径
    :param target_url: 目标URL
    :return: True如果文件存在且URL匹配，False否则
    """
    if not os.path.exists(file_path):
        return False
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
            # 检查第一行是否为引用块且包含目标URL
            if first_line.startswith('> ') and target_url in first_line:
                return True
    except:
        pass
    
    return False


def get_unique_filename(base_dir, title, url):
    """
    获取唯一的文件名，如果标题重复则添加URL的ID部分
    :param base_dir: 基础目录
    :param title: 文章标题
    :param url: 文章URL
    :return: 唯一的文件路径
    """
    base_filename = filter_title_str(title)
    file_path = os.path.join(base_dir, base_filename + ".md")
    
    # 如果文件不存在，直接返回
    if not os.path.exists(file_path):
        return file_path
    
    # 如果文件存在且URL匹配，返回该路径（用于跳过）
    if is_article_already_downloaded(file_path, url):
        return file_path
    
    # 如果文件存在但URL不匹配，添加URL ID后缀
    url_id = url.split('/')[-1]
    unique_filename = f"{base_filename}_{url_id}"
    return os.path.join(base_dir, unique_filename + ".md")




def save_processing_log():
    """
    保存处理日志到logs目录
    """
    logs_dir = pathlib.Path(logs_dir) if logs_dir else DEFAULT_OUTPUT_ROOT / 'logs'
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"{timestamp}.json"
    log_path = os.path.join(logs_dir, log_filename)
    
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(processing_log, f, ensure_ascii=False, indent=2)
    
    print(f"处理日志已保存到: {log_path}")



def _default_fetch_metadata(item):
    if item.source_type == "answer":
        return fetch_answer_metadata(item.url)
    if item.source_type == "article":
        return fetch_article_metadata(item.url)
    return SourceMetadata(item.url, item.source_type, item.source_id, None)


def _default_render_markdown_for(assets_dir):
    """构造默认渲染函数：正文前加原始 URL 引用行，资产写入 assets_dir。"""
    def render(snapshot, item):
        body = render_markdown(snapshot.html, assets_dir, heading_style="ATX")
        return f"> {item.url}\n{body}"
    return render


# 清单写入/保存互斥锁：项级并发导出时保护 manifest.records 与 save() 序列化
_manifest_lock = threading.Lock()
# 项级并发数：控制正文 API 与渲染的并发峰值，避免触发知乎限流
ITEM_EXPORT_WORKERS = 3


def export_item_with_integrity(
    item,
    collection_dir,
    manifest,
    mode=ExportMode.BALANCED,
    fetch_metadata_fn=None,
    fetch_snapshot_fn=None,
    render_markdown_fn=None,
):
    """Verify, adopt, repair, or refresh one collection item."""
    collection_path = pathlib.Path(collection_dir)
    collection_path.mkdir(parents=True, exist_ok=True)
    assets_dir = collection_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    fetch_metadata_fn = fetch_metadata_fn or _default_fetch_metadata
    fetch_snapshot_fn = fetch_snapshot_fn or fetch_source_snapshot
    render_markdown_fn = render_markdown_fn or _default_render_markdown_for(assets_dir)

    record = manifest.records.get(item.url)
    if record and record.get("markdown_path"):
        file_path = collection_path / record["markdown_path"]
    else:
        file_path = pathlib.Path(get_unique_filename(str(collection_path), item.title, item.url))

    metadata = None
    if item.updated_time is not None:
        # 收藏夹分页响应自带 updated_time（与回答/专栏 API 同源同值），直接复用，
        # 省去逐项元数据请求；时间戳未变化且本地完好时整项零网络请求。
        metadata = SourceMetadata(item.url, item.source_type, item.source_id, item.updated_time)
    else:
        try:
            metadata = fetch_metadata_fn(item)
        except Exception as exc:
            return {
                "name": item.title,
                "url": item.url,
                "status": "metadata_failed",
                "issues": [{"code": "metadata_failed", "message": str(exc)}],
            }

    local_intact = local_record_is_intact(record, file_path, assets_dir)
    action = decide_action(
        mode,
        file_exists=file_path.exists(),
        record=record,
        local_intact=local_intact,
        metadata=metadata,
    )
    if action == IntegrityAction.SKIP_VERIFIED:
        return {
            "name": item.title,
            "url": item.url,
            "status": "skipped_verified",
            "action": action.value,
            "markdown_path": file_path.name,
            "issues": [],
            "warnings": record.get("warnings", []) if record else [],
        }

    try:
        snapshot = fetch_snapshot_fn(item.url)
    except Exception as exc:
        return {
            "name": item.title,
            "url": item.url,
            "status": "source_failed",
            "action": action.value,
            "issues": [{"code": "source_failed", "message": str(exc)}],
        }

    existing_result = None
    existing_markdown = None
    if file_path.exists():
        try:
            existing_markdown = file_path.read_text(encoding="utf-8")
            existing_result = validate_markdown(snapshot, existing_markdown, assets_dir)
        except OSError as exc:
            existing_result = None
            logging.warning(f"读取已有Markdown失败: {file_path}: {exc}")

    if action in {IntegrityAction.FETCH_AND_VERIFY, IntegrityAction.FETCH_AND_AUDIT}:
        if existing_result and existing_result.valid:
            with _manifest_lock:
                manifest.records[item.url] = record_from_validation(
                    snapshot,
                    file_path,
                    existing_markdown,
                    existing_result,
                    item.title,
                )
            status = "audit_verified" if mode in {ExportMode.AUDIT, ExportMode.AUDIT_REPAIR} else "adopted"
            return {
                "name": item.title,
                "url": item.url,
                "status": status,
                "action": action.value,
                "markdown_path": file_path.name,
                "text_coverage": existing_result.text_coverage,
                "issues": [],
                "warnings": [warning.__dict__ for warning in existing_result.warnings],
            }
        if mode == ExportMode.AUDIT:
            issues = existing_result.issues if existing_result else []
            return {
                "name": item.title,
                "url": item.url,
                "status": "invalid",
                "action": action.value,
                "markdown_path": file_path.name,
                "issues": [issue.__dict__ for issue in issues],
                "warnings": [
                    warning.__dict__ for warning in (existing_result.warnings if existing_result else [])
                ],
            }

    try:
        # 渲染前并发预取图片：convert_img 命中本地非空文件即不再发请求，
        # 将图片下载从逐张串行改为小线程池并发（图片下载是长文章的主要耗时）。
        prefetch_images(snapshot.image_urls, assets_dir)
        rendered = render_markdown_fn(snapshot, item)
        rendered_result = validate_markdown(snapshot, rendered, assets_dir)
    except Exception as exc:
        return {
            "name": item.title,
            "url": item.url,
            "status": "render_failed",
            "action": action.value,
            "issues": [{"code": "render_failed", "message": str(exc)}],
        }
    if not rendered_result.valid:
        return {
            "name": item.title,
            "url": item.url,
            "status": "render_invalid",
            "action": action.value,
            "issues": [issue.__dict__ for issue in rendered_result.issues],
            "warnings": [warning.__dict__ for warning in rendered_result.warnings],
        }

    atomic_write_text(file_path, rendered)
    final_markdown = file_path.read_text(encoding="utf-8")
    final_result = validate_markdown(snapshot, final_markdown, assets_dir)
    if not final_result.valid:
        return {
            "name": item.title,
            "url": item.url,
            "status": "final_invalid",
            "action": action.value,
            "issues": [issue.__dict__ for issue in final_result.issues],
            "warnings": [warning.__dict__ for warning in final_result.warnings],
        }
    with _manifest_lock:
        manifest.records[item.url] = record_from_validation(
            snapshot,
            file_path,
            final_markdown,
            final_result,
            item.title,
        )
    if mode == ExportMode.FORCE:
        status = "refreshed"
    elif file_path.exists() and existing_markdown is not None:
        status = "repaired"
    else:
        status = "downloaded"
    return {
        "name": item.title,
        "url": item.url,
        "status": status,
        "action": action.value,
        "markdown_path": file_path.name,
        "text_coverage": final_result.text_coverage,
        "issues": [],
        "warnings": [warning.__dict__ for warning in final_result.warnings],
    }


def export_collection_with_integrity(
    collection_name,
    collection_url,
    mode=ExportMode.BALANCED,
    fetch_result=None,
    output_root=None,
    fetch_snapshot_fn=None,
    render_markdown_fn=None,
):
    """Export one collection and persist its manifest after each verified item.

    output_root 显式指定输出根目录（默认项目内 downloads/）；网络依赖可
    通过 fetch_snapshot_fn / render_markdown_fn 注入（与项级注入点一致）。
    """
    collection_id = collection_url.split('?')[0].rstrip('/').split('/')[-1]
    result = fetch_result or fetch_collection_items(collection_id)
    collection_report = {
        "name": collection_name,
        "url": collection_url,
        "collection": result.to_dict(),
        "items": [],
    }
    if not result.complete:
        collection_report["status"] = "collection_incomplete"
        return collection_report

    root = pathlib.Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT
    collection_dir = collection_output_dir(root, collection_name)
    collection_dir.mkdir(parents=True, exist_ok=True)
    manifest = IntegrityManifestStore.load(
        collection_dir / ".zhihu-integrity.json",
        expected_collection_id=collection_id,
        collection_url=collection_url,
    )
    total_items = len(result.exportable_items)
    # 项级并发导出：IO 密集（正文 API + 图片下载），并发数由 ITEM_EXPORT_WORKERS 控制；
    # 清单读写通过 _manifest_lock 互斥，每项完成后仍即时落盘。
    with ThreadPoolExecutor(max_workers=ITEM_EXPORT_WORKERS) as executor:
        futures = []
        for index, item in enumerate(result.exportable_items, start=1):
            logging.info(f"校验 {collection_name} [{index}/{total_items}]: {item.title}")
            futures.append(
                executor.submit(
                    export_item_with_integrity,
                    item,
                    collection_dir,
                    manifest,
                    mode=mode,
                    fetch_snapshot_fn=fetch_snapshot_fn,
                    render_markdown_fn=render_markdown_fn,
                )
            )
        for future in as_completed(futures):
            item_report = future.result()
            collection_report["items"].append(item_report)
            try:
                if item_report.get("url") in manifest.records:
                    with _manifest_lock:
                        manifest.save()
            except OSError as exc:
                # 同步盘锁定等瞬时故障：清单在下一项完成时会再次落盘，不应让整项/收藏夹失败
                logging.warning(f"清单保存失败（将在下一项完成时重试）: {exc}")
    failures = [
        item for item in collection_report["items"]
        if item["status"] in {
            "metadata_failed", "source_failed", "render_failed", "render_invalid",
            "final_invalid", "invalid"
        }
    ]
    has_warnings = bool(result.total_mismatch) or any(
        item.get("warnings") for item in collection_report["items"]
    )
    if failures:
        collection_report["status"] = "invalid"
    elif has_warnings:
        collection_report["status"] = "verified_with_warnings"
    else:
        collection_report["status"] = "verified"
    return collection_report



def determine_exit_code(collection_reports):
    if any(
        report.get("status") in {"collection_incomplete", "manifest_error", "config_error"}
        or not report.get("collection", {}).get("complete", True)
        for report in collection_reports
    ):
        return 2
    failing_statuses = {
        "invalid",
        "metadata_failed",
        "source_failed",
        "render_failed",
        "render_invalid",
        "final_invalid",
    }
    if any(
        report.get("status") == "invalid"
        or any(item.get("status") in failing_statuses for item in report.get("items", []))
        for report in collection_reports
    ):
        return 1
    return 0


def save_integrity_report(collection_reports, mode, logs_dir=None, timestamp=None):
    """把运行报告写成 JSON；logs_dir 缺省时写到默认输出根的 logs/ 目录。"""
    directory = pathlib.Path(logs_dir) if logs_dir else DEFAULT_OUTPUT_ROOT / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    exit_code = determine_exit_code(collection_reports)
    report = RunReport(mode=mode, collections=collection_reports, exit_code=exit_code)
    path = directory / f"integrity_{timestamp}.json"
    atomic_write_json(path, report.to_dict())
    return path


