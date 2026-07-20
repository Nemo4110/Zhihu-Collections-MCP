# -*- coding: utf-8 -*-
"""Pure helpers for validating and persisting Zhihu export artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

SCHEMA_VERSION = 1
TEXT_COVERAGE_THRESHOLD = 0.98


@dataclass(frozen=True)
class SourceMetadata:
    canonical_url: str
    source_type: str
    source_id: str
    updated_time: int | None = None


@dataclass(frozen=True)
class SourceSnapshot:
    metadata: SourceMetadata
    candidate: str
    html: str
    text_segments: tuple[str, ...]
    image_urls: tuple[str, ...]


@dataclass(frozen=True)
class IntegrityIssue:
    code: str
    message: str


@dataclass
class IntegrityResult:
    valid: bool
    status: str
    text_coverage: float
    issues: list[IntegrityIssue] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)


_ANSWER_RE = re.compile(r"/question/\d+/answer/(\d+)")
_ARTICLE_RE = re.compile(r"/(?:p/)(\d+)")


def canonicalize_url(url: str) -> str:
    """Return a stable URL for supported Zhihu answers and articles."""
    value = (url or "").strip()
    parts = urlsplit(value)
    path = parts.path.rstrip("/")
    answer_match = _ANSWER_RE.search(path)
    if answer_match:
        question_match = re.search(r"/question/(\d+)/answer/", path)
        question_id = question_match.group(1) if question_match else ""
        return f"https://www.zhihu.com/question/{question_id}/answer/{answer_match.group(1)}"
    article_match = _ARTICLE_RE.fullmatch(path)
    if article_match and parts.netloc in {"www.zhihu.com", "zhuanlan.zhihu.com"}:
        return f"https://zhuanlan.zhihu.com/p/{article_match.group(1)}"
    return f"https://{parts.netloc.lower()}{path}" if parts.netloc else path


def parse_source_identity(url: str) -> tuple[str, str]:
    canonical = canonicalize_url(url)
    answer_match = _ANSWER_RE.search(canonical)
    if answer_match:
        return "answer", answer_match.group(1)
    article_match = re.search(r"zhuanlan\.zhihu\.com/p/(\d+)$", canonical)
    if article_match:
        return "article", article_match.group(1)
    raise ValueError(f"Unsupported Zhihu content URL: {url}")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: str | Path, text: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            delete=False,
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: str | Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_WHITESPACE_RE = re.compile(r"\s+")
_CONTENT_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "td", "th", "pre")


def normalize_visible_text(value: str) -> str:
    value = _ZERO_WIDTH_RE.sub("", value or "")
    return _WHITESPACE_RE.sub(" ", value).strip()


def _combine_short_segments(segments: list[str], minimum: int = 8) -> tuple[str, ...]:
    combined: list[str] = []
    pending = ""
    for segment in segments:
        current = normalize_visible_text(segment)
        if not current:
            continue
        if pending:
            current = normalize_visible_text(f"{pending} {current}")
            pending = ""
        if len(current) < minimum:
            pending = current
        else:
            combined.append(current)
    if pending:
        if combined:
            combined[-1] = normalize_visible_text(f"{combined[-1]} {pending}")
        else:
            combined.append(pending)
    return tuple(combined)


def extract_text_segments(html: str) -> tuple[str, ...]:
    soup = BeautifulSoup(html or "", "lxml")
    for element in soup.find_all(("script", "style")):
        element.decompose()
    values: list[str] = []
    for element in soup.find_all(_CONTENT_TAGS):
        if element.find(_CONTENT_TAGS):
            continue
        value = normalize_visible_text(element.get_text(" ", strip=True))
        if value:
            values.append(value)
    if not values:
        fallback = normalize_visible_text(soup.get_text(" ", strip=True))
        if fallback:
            values.append(fallback)
    return _combine_short_segments(values)


def image_filename_from_url(url: str) -> str:
    name = Path(unquote(urlsplit(url).path)).name
    if name:
        return name
    return f"image-{sha256_text(url)[:16]}.bin"


def extract_image_urls(html: str) -> tuple[str, ...]:
    soup = BeautifulSoup(html or "", "lxml")
    seen: set[str] = set()
    urls: list[str] = []
    for image in soup.find_all("img"):
        src = (image.get("data-original") or image.get("src") or "").strip()
        if not src or src.startswith("data:") or "data:image/svg+xml" in src:
            continue
        if src not in seen:
            seen.add(src)
            urls.append(src)
    return tuple(urls)


def snapshot_from_html(
    metadata: SourceMetadata,
    candidate: str,
    html: str,
) -> SourceSnapshot:
    return SourceSnapshot(
        metadata=metadata,
        candidate=candidate,
        html=html,
        text_segments=extract_text_segments(html),
        image_urls=extract_image_urls(html),
    )


def markdown_visible_text(markdown: str) -> str:
    value = markdown or ""
    value = re.sub(r"!\[\[([^\]]+)\]\]", r" \1 ", value)
    value = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r" \1 ", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r" \1 ", value)
    value = re.sub(r"`{1,3}([^`]*)`{1,3}", r" \1 ", value, flags=re.DOTALL)
    value = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|>|[-+*]|\d+[.)])\s*", "", value)
    value = value.translate(str.maketrans({char: " " for char in "*_~[]"}))
    return normalize_visible_text(value)


def weighted_text_coverage(segments: tuple[str, ...], markdown_text: str) -> float:
    if not segments:
        return 1.0
    normalized_markdown = normalize_visible_text(markdown_text)
    total = sum(len(segment) for segment in segments)
    covered = sum(len(segment) for segment in segments if segment in normalized_markdown)
    return covered / total if total else 1.0


def validate_markdown(
    snapshot: SourceSnapshot,
    markdown: str,
    assets_dir: str | Path,
    threshold: float = TEXT_COVERAGE_THRESHOLD,
) -> IntegrityResult:
    issues: list[IntegrityIssue] = []
    assets: list[dict[str, Any]] = []
    if not (markdown or "").strip():
        issues.append(IntegrityIssue("empty_markdown", "Markdown file is empty"))
    if snapshot.metadata.canonical_url not in markdown:
        issues.append(IntegrityIssue("missing_source_url", "Canonical source URL is missing"))

    visible_markdown = markdown_visible_text(markdown)
    coverage = weighted_text_coverage(snapshot.text_segments, visible_markdown)
    if snapshot.text_segments:
        if snapshot.text_segments[0] not in visible_markdown:
            issues.append(IntegrityIssue("missing_first_segment", "First source text segment is missing"))
        if snapshot.text_segments[-1] not in visible_markdown:
            issues.append(IntegrityIssue("missing_last_segment", "Last source text segment is missing"))
        if coverage < threshold:
            issues.append(
                IntegrityIssue(
                    "low_text_coverage",
                    f"Text coverage {coverage:.4f} is below {threshold:.4f}",
                )
            )
    elif not snapshot.image_urls:
        issues.append(IntegrityIssue("empty_source", "Source contains no visible text or images"))

    asset_root = Path(assets_dir)
    for image_url in snapshot.image_urls:
        filename = image_filename_from_url(image_url)
        asset_path = asset_root / filename
        if filename not in markdown:
            status = "missing_reference"
            issues.append(IntegrityIssue("missing_asset_reference", f"Markdown does not reference {filename}"))
        elif not asset_path.exists():
            status = "missing"
            issues.append(IntegrityIssue("missing_asset", f"Missing asset {filename}"))
        elif asset_path.stat().st_size <= 0:
            status = "empty"
            issues.append(IntegrityIssue("empty_asset", f"Empty asset {filename}"))
        else:
            status = "verified"
        assets.append(
            {
                "url": image_url,
                "filename": filename,
                "size": asset_path.stat().st_size if asset_path.exists() else 0,
                "status": status,
            }
        )

    return IntegrityResult(
        valid=not issues,
        status="verified" if not issues else "invalid",
        text_coverage=coverage,
        issues=issues,
        assets=assets,
    )


class ManifestSchemaError(ValueError):
    """Raised when an integrity manifest cannot be safely interpreted."""


class ExportMode(str, Enum):
    BALANCED = "balanced"
    AUDIT = "audit"
    AUDIT_REPAIR = "audit_repair"
    FORCE = "force"


class IntegrityAction(str, Enum):
    SKIP_VERIFIED = "skip_verified"
    FETCH_AND_VERIFY = "fetch_and_verify"
    FETCH_AND_AUDIT = "fetch_and_audit"
    FETCH_AND_WRITE = "fetch_and_write"


@dataclass
class IntegrityManifestStore:
    path: Path
    collection_id: str
    collection_url: str = ""
    records: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        path: str | Path,
        expected_collection_id: str,
        collection_url: str = "",
    ) -> "IntegrityManifestStore":
        manifest_path = Path(path)
        if not manifest_path.exists():
            return cls(manifest_path, str(expected_collection_id), collection_url, {})
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestSchemaError(f"Cannot read integrity manifest: {exc}") from exc
        if not isinstance(payload, dict):
            raise ManifestSchemaError("Integrity manifest root must be an object")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ManifestSchemaError(
                f"Unsupported integrity manifest schema: {payload.get('schema_version')!r}"
            )
        collection_id = str(payload.get("collection_id", ""))
        if collection_id != str(expected_collection_id):
            raise ManifestSchemaError(
                f"Manifest collection ID {collection_id!r} does not match {expected_collection_id!r}"
            )
        items = payload.get("items", {})
        if not isinstance(items, dict):
            raise ManifestSchemaError("Integrity manifest items must be an object")
        return cls(
            manifest_path,
            collection_id,
            str(payload.get("collection_url") or collection_url or ""),
            items,
        )

    def save(self) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "collection_id": self.collection_id,
            "collection_url": self.collection_url,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "items": self.records,
        }
        atomic_write_json(self.path, payload)


def local_record_is_intact(
    record: dict[str, Any] | None,
    markdown_path: str | Path,
    assets_dir: str | Path,
) -> bool:
    if not record or record.get("status") != "verified":
        return False
    path = Path(markdown_path)
    if not path.is_file() or not record.get("markdown_sha256"):
        return False
    try:
        if sha256_file(path) != record["markdown_sha256"]:
            return False
    except OSError:
        return False
    asset_root = Path(assets_dir)
    for asset in record.get("assets", []):
        filename = asset.get("filename")
        if not filename or asset.get("status") != "verified":
            return False
        asset_path = asset_root / filename
        if not asset_path.is_file() or asset_path.stat().st_size <= 0:
            return False
        expected_size = asset.get("size")
        if expected_size is not None and int(expected_size) != asset_path.stat().st_size:
            return False
    return True


def decide_action(
    mode: ExportMode,
    *,
    file_exists: bool,
    record: dict[str, Any] | None,
    local_intact: bool,
    metadata: SourceMetadata,
) -> IntegrityAction:
    if mode == ExportMode.FORCE:
        return IntegrityAction.FETCH_AND_WRITE
    if mode in {ExportMode.AUDIT, ExportMode.AUDIT_REPAIR}:
        return IntegrityAction.FETCH_AND_AUDIT
    if not file_exists:
        return IntegrityAction.FETCH_AND_WRITE
    if not record:
        return IntegrityAction.FETCH_AND_VERIFY
    if not local_intact:
        return IntegrityAction.FETCH_AND_WRITE
    previous_updated = record.get("source_updated_time")
    if metadata.updated_time is None:
        if metadata.source_type == "article":
            return IntegrityAction.FETCH_AND_VERIFY
    elif previous_updated != metadata.updated_time:
        return IntegrityAction.FETCH_AND_WRITE
    return IntegrityAction.SKIP_VERIFIED


def record_from_validation(
    snapshot: SourceSnapshot,
    markdown_path: str | Path,
    markdown: str,
    result: IntegrityResult,
    title: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "source_type": snapshot.metadata.source_type,
        "source_id": snapshot.metadata.source_id,
        "source_updated_time": snapshot.metadata.updated_time,
        "source_candidate": snapshot.candidate,
        "source_content_sha256": sha256_text(snapshot.html),
        "source_text_length": sum(len(segment) for segment in snapshot.text_segments),
        "source_image_count": len(snapshot.image_urls),
        "markdown_path": Path(markdown_path).name,
        "markdown_sha256": sha256_text(markdown),
        "markdown_text_length": len(markdown_visible_text(markdown)),
        "text_coverage": result.text_coverage,
        "assets": result.assets,
        "last_verified_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": result.status,
        "issues": [issue.__dict__ for issue in result.issues],
    }
