# -*- coding: utf-8 -*-
"""Pure helpers for validating and persisting Zhihu export artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
