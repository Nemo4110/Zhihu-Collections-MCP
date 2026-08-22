# -*- coding: utf-8 -*-
"""Migrate legacy Obsidian image embeds in exported files to standard Markdown."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from integrity import (
    atomic_write_json,
    atomic_write_text,
    equation_tex_from_url,
    is_equation_url,
    markdown_visible_text,
    sha256_text,
)

_LEGACY_IMAGE_RE = re.compile(r"!\[\[([^\]\r\n]+)\]\]")


@dataclass
class MigrationStats:
    files_changed: int = 0
    images_changed: int = 0
    equations_changed: int = 0
    unresolved_equations: int = 0
    manifests_changed: int = 0


def _consume_legacy_alt(markdown: str, start: int) -> tuple[str, int] | None:
    """Read the converter's legacy newline + ``(alt)`` suffix."""
    position = start
    if markdown.startswith("\r\n", position):
        position += 2
    elif markdown.startswith("\n", position):
        position += 1
    else:
        return None

    while position < len(markdown) and markdown[position] in " \t":
        position += 1
    if position >= len(markdown) or markdown[position] != "(":
        return None

    opening = position
    depth = 0
    escaped = False
    for position in range(opening, len(markdown)):
        char = markdown[position]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                end = position + 1
                alt = markdown[opening + 1 : position]
                return alt, end
        elif char in "\r\n" and depth == 1:
            return None
    return None


def _is_standalone(markdown: str, token_start: int) -> bool:
    line_start = markdown.rfind("\n", 0, token_start) + 1
    return not markdown[line_start:token_start].strip()


def migrate_markdown(
    markdown: str,
    *,
    equation_fallbacks: list[str] | None = None,
) -> tuple[str, MigrationStats]:
    fallbacks = iter(equation_fallbacks or [])
    stats = MigrationStats()
    pieces: list[str] = []
    cursor = 0

    for match in _LEGACY_IMAGE_RE.finditer(markdown):
        if match.start() < cursor:
            continue
        filename = match.group(1)
        caption = _consume_legacy_alt(markdown, match.end())
        alt = caption[0] if caption else ""
        consumed_end = caption[1] if caption else match.end()
        standalone = _is_standalone(markdown, match.start())

        pieces.append(markdown[cursor : match.start()])
        if filename == "equation":
            tex = alt
            if not tex:
                tex = next(fallbacks, "")
            if tex:
                display = standalone or len(tex) > 70
                if display:
                    prefix = "" if standalone else "\n\n"
                    replacement = f"{prefix}$$\n{tex}\n$$"
                else:
                    replacement = f"${tex}$"
                stats.equations_changed += 1
            else:
                replacement = ""
                stats.unresolved_equations += 1
        else:
            escaped_alt = alt.replace("\n", " ").replace("]", r"\]")
            asset_path = quote(f"assets/{filename}", safe="/-._~")
            replacement = f"![{escaped_alt}]({asset_path})"
            stats.images_changed += 1

        pieces.append(replacement)
        cursor = consumed_end

    pieces.append(markdown[cursor:])
    migrated = "".join(pieces)

    def display_long_math(match: re.Match[str]) -> str:
        tex = match.group(1)
        if len(tex) <= 70:
            return match.group(0)
        stats.equations_changed += 1
        return f"\n\n$$\n{tex}\n$$\n\n"

    if stats.equations_changed:
        migrated = re.sub(
            r"(?<!\$)\$(?!\$)([^\n]*?)(?<!\$)\$(?!\$)",
            display_long_math,
            migrated,
        )
        migrated = re.sub(r"(\$[^$\n]+\$)(?=\d+[.]\s)", r"\1\n", migrated)
    return migrated, stats


def _manifest_equations(record: dict) -> list[str]:
    return [
        equation_tex_from_url(asset.get("url", ""))
        for asset in record.get("assets", [])
        if is_equation_url(asset.get("url", ""))
    ]


def _update_manifest_record(record: dict, markdown: str) -> bool:
    original_assets = record.get("assets", [])
    assets = [asset for asset in original_assets if not is_equation_url(asset.get("url", ""))]
    changed = assets != original_assets
    markdown_hash = sha256_text(markdown)
    markdown_length = len(markdown_visible_text(markdown))
    if record.get("markdown_sha256") != markdown_hash:
        record["markdown_sha256"] = markdown_hash
        changed = True
    if record.get("markdown_text_length") != markdown_length:
        record["markdown_text_length"] = markdown_length
        changed = True
    if record.get("source_image_count") != len(assets):
        record["source_image_count"] = len(assets)
        changed = True
    if changed:
        record["assets"] = assets
    return changed


def migrate_output(root: Path, *, write: bool) -> MigrationStats:
    root = root.resolve()
    stats = MigrationStats()
    manifests: dict[Path, dict] = {}
    records_by_path: dict[Path, tuple[dict, dict]] = {}

    for manifest_path in root.rglob(".zhihu-integrity.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifests[manifest_path] = manifest
        for record in manifest.get("items", {}).values():
            markdown_name = record.get("markdown_path")
            if markdown_name:
                records_by_path[(manifest_path.parent / markdown_name).resolve()] = (manifest, record)

    for markdown_path in root.rglob("*.md"):
        markdown = markdown_path.read_text(encoding="utf-8")
        manifest_record = records_by_path.get(markdown_path.resolve())
        fallbacks = _manifest_equations(manifest_record[1]) if manifest_record else []
        migrated, file_stats = migrate_markdown(markdown, equation_fallbacks=fallbacks)
        stats.images_changed += file_stats.images_changed
        stats.equations_changed += file_stats.equations_changed
        stats.unresolved_equations += file_stats.unresolved_equations
        if migrated == markdown:
            continue
        stats.files_changed += 1
        if write:
            atomic_write_text(markdown_path, migrated)
        if manifest_record and _update_manifest_record(manifest_record[1], migrated):
            manifest_record[0]["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    for manifest_path, manifest in manifests.items():
        if not any(manifest is pair[0] for pair in records_by_path.values()):
            continue
        original = manifest_path.read_text(encoding="utf-8")
        rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if rendered != original:
            stats.manifests_changed += 1
            if write:
                atomic_write_json(manifest_path, manifest)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="output", type=Path)
    parser.add_argument("--write", action="store_true", help="apply changes; otherwise only report")
    args = parser.parse_args()
    stats = migrate_output(args.root, write=args.write)
    mode = "updated" if args.write else "would update"
    print(
        f"{mode}: {stats.files_changed} files, {stats.images_changed} images, "
        f"{stats.equations_changed} equations, {stats.manifests_changed} manifests"
    )
    if stats.unresolved_equations:
        print(f"unresolved equations: {stats.unresolved_equations}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
