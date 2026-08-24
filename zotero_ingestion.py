from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

try:
    import requests
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("This script requires the 'requests' package in the active Python environment.") from exc

IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+['\"][^)]*['\"])?\)")
SKIPPED_SCHEMES = ("http://", "https://", "data:", "mailto:", "zotero:", "#")


@dataclass(frozen=True)
class Entry:
    title: str
    url: str
    item_type: str
    markdown_path: Path
    asset_root: Path
    fields: dict[str, Any]
    creators: list[dict[str, Any]]
    attachment_title: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Markdown attachments through the Zotero 10 Local API")
    parser.add_argument("manifest", type=Path, help="JSON manifest described in docs/zotero-ingestion-manifest.md")
    parser.add_argument("--api-base", default="http://localhost:23119/api")
    parser.add_argument("--app-name", default="Codex Zotero Markdown Ingestion")
    parser.add_argument("--report", type=Path, help="JSON result path (default: next to manifest)")
    parser.add_argument("--dry-run", action="store_true", help="Validate manifest and inspect Zotero without writing")
    parser.add_argument(
        "--update-existing-metadata",
        action="store_true",
        help="Explicitly replace fields and creators on parents reused by URL",
    )
    parser.add_argument(
        "--migrate-item-types",
        action="store_true",
        help="Explicitly migrate a reused parent when its current type differs from the manifest",
    )
    return parser.parse_args()


def load_entries(manifest_path: Path) -> tuple[str | None, list[Entry]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError("Manifest must be an object with an entries array")
    collection_key = payload.get("collection_key")
    if collection_key is not None and (not isinstance(collection_key, str) or not collection_key.strip()):
        raise ValueError("collection_key must be a nonempty string when supplied")

    entries: list[Entry] = []
    seen_urls: set[str] = set()
    for index, raw in enumerate(payload["entries"], start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"entries[{index}] must be an object")
        missing = [name for name in ("title", "url", "item_type", "markdown_path") if not raw.get(name)]
        if missing:
            raise ValueError(f"entries[{index}] missing required fields: {', '.join(missing)}")
        url = str(raw["url"]).strip()
        if url in seen_urls:
            raise ValueError(f"Duplicate URL in manifest: {url}")
        seen_urls.add(url)
        markdown_path = Path(raw["markdown_path"])
        if not markdown_path.is_absolute():
            markdown_path = (manifest_path.parent / markdown_path).resolve()
        else:
            markdown_path = markdown_path.resolve()
        if not markdown_path.is_file() or markdown_path.stat().st_size <= 0:
            raise ValueError(f"Markdown file is missing or empty: {markdown_path}")
        asset_root_value = raw.get("asset_root")
        asset_root = (Path(asset_root_value) if asset_root_value else markdown_path.parent)
        if not asset_root.is_absolute():
            asset_root = (manifest_path.parent / asset_root).resolve()
        else:
            asset_root = asset_root.resolve()
        fields = raw.get("fields") or {}
        creators = raw.get("creators") or []
        if not isinstance(fields, dict) or not isinstance(creators, list):
            raise ValueError(f"entries[{index}] fields must be an object and creators must be an array")
        entries.append(
            Entry(
                title=str(raw["title"]).strip(),
                url=url,
                item_type=str(raw["item_type"]).strip(),
                markdown_path=markdown_path,
                asset_root=asset_root,
                fields=fields,
                creators=creators,
                attachment_title=str(raw.get("attachment_title") or f"Markdown — {markdown_path.stem}"),
            )
        )
    return collection_key, entries


def referenced_local_images(entry: Entry) -> list[Path]:
    text = entry.markdown_path.read_text(encoding="utf-8")
    references: set[Path] = set()
    for match in IMAGE_LINK_RE.finditer(text):
        value = (match.group(1) or match.group(2) or "").strip()
        if not value or value.startswith(SKIPPED_SCHEMES):
            continue
        value = value.replace("\\", "/")
        candidate = (entry.asset_root / value).resolve()
        if entry.asset_root not in candidate.parents:
            raise ValueError(f"Image path escapes asset_root for {entry.markdown_path.name}: {value}")
        if not candidate.is_file():
            raise ValueError(f"Referenced image is missing for {entry.markdown_path.name}: {value}")
        references.add(candidate)
    return sorted(references)


class ZoteroLocalApi:
    def __init__(self, api_base: str, app_name: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.app_name = app_name
        self.session = requests.Session()
        root = self.session.get(f"{self.api_base}/", timeout=30)
        root.raise_for_status()
        self.server_id = root.headers.get("Zotero-Server-ID")
        if not self.server_id:
            raise RuntimeError("Zotero 10+ Local API is required (Zotero-Server-ID was not returned)")
        self.api_key: str | None = None

    def authorize(self) -> None:
        response = self.session.post(
            f"{self.api_base}/local/authorize",
            headers={"Content-Type": "application/json", "Zotero-Server-ID": self.server_id},
            json={"appName": self.app_name},
            timeout=300,
        )
        response.raise_for_status()
        key = response.json().get("key")
        if not key:
            raise RuntimeError("Zotero Local API authorization did not return a key")
        self.api_key = key

    def _write(self, method: str, path: str, *, json_body: Any = None, data: Any = None, version: int | None = None) -> requests.Response:
        if not self.api_key:
            self.authorize()
        headers = {"Zotero-API-Key": self.api_key or "", "Zotero-Server-ID": self.server_id, "Zotero-Write-Token": uuid.uuid4().hex}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if version is not None:
            headers["If-Unmodified-Since-Version"] = str(version)
        response = self.session.request(method, f"{self.api_base}{path}", headers=headers, json=json_body, data=data, timeout=180)
        if response.status_code == 401:
            self.authorize()
            headers["Zotero-API-Key"] = self.api_key or ""
            headers["Zotero-Write-Token"] = uuid.uuid4().hex
            response = self.session.request(method, f"{self.api_base}{path}", headers=headers, json=json_body, data=data, timeout=180)
        return response

    def all_regular_items(self) -> dict[str, dict[str, Any]]:
        by_url: dict[str, dict[str, Any]] = {}
        start = 0
        while True:
            response = self.session.get(f"{self.api_base}/users/0/items", params={"format": "json", "limit": 100, "start": start}, timeout=60)
            response.raise_for_status()
            page = response.json()
            for raw in page:
                data = raw.get("data", {})
                if data.get("itemType") in {"attachment", "note", "annotation"}:
                    continue
                url = (data.get("url") or "").strip()
                if url and url not in by_url:
                    by_url[url] = data
            if len(page) < 100:
                return by_url
            start += len(page)

    def get_item(self, key: str) -> dict[str, Any]:
        response = self.session.get(f"{self.api_base}/users/0/items/{key}", timeout=60)
        response.raise_for_status()
        return response.json()["data"]

    def create_parent(self, entry: Entry, collection_key: str | None) -> str:
        payload: dict[str, Any] = {"itemType": entry.item_type, "title": entry.title, "url": entry.url, **entry.fields}
        if entry.creators:
            payload["creators"] = entry.creators
        if collection_key:
            payload["collections"] = [collection_key]
        response = self._write("POST", "/users/0/items", json_body=[payload])
        response.raise_for_status()
        result = response.json()
        if result.get("failed") or "0" not in result.get("success", {}):
            raise RuntimeError(f"Parent creation failed for {entry.url}: {result}")
        return result["success"]["0"]

    def update_parent(self, current: dict[str, Any], entry: Entry, collection_key: str | None, update_metadata: bool, migrate_type: bool) -> None:
        changed = False
        updated = dict(current)
        if collection_key and collection_key not in updated.get("collections", []):
            updated["collections"] = [*updated.get("collections", []), collection_key]
            changed = True
        if updated.get("itemType") != entry.item_type:
            if not migrate_type:
                raise RuntimeError(f"Existing item {current['key']} has type {updated.get('itemType')!r}; use --migrate-item-types to change it")
            updated["itemType"] = entry.item_type
            changed = True
        if update_metadata:
            updated.update({"title": entry.title, "url": entry.url, **entry.fields})
            updated["creators"] = entry.creators
            changed = True
        if not changed:
            return
        response = self._write("PUT", f"/users/0/items/{current['key']}", json_body=updated, version=current["version"])
        if response.status_code != 204:
            raise RuntimeError(f"Parent update failed for {current['key']}: HTTP {response.status_code}")

    def find_markdown_attachment(self, parent_key: str, title: str) -> str | None:
        response = self.session.get(f"{self.api_base}/users/0/items/{parent_key}/children", timeout=60)
        response.raise_for_status()
        for child in response.json():
            data = child.get("data", {})
            if data.get("itemType") == "attachment" and data.get("contentType") == "text/markdown" and data.get("title") == title:
                return data.get("key")
        return None

    def create_attachment(self, parent_key: str, entry: Entry) -> str:
        payload = {
            "itemType": "attachment",
            "parentItem": parent_key,
            "linkMode": "imported_file",
            "title": entry.attachment_title,
            "contentType": "text/markdown",
            "charset": "utf-8",
            "filename": entry.markdown_path.name,
            "note": "",
            "tags": [],
            "relations": {},
        }
        response = self._write("POST", "/users/0/items", json_body=[payload])
        response.raise_for_status()
        result = response.json()
        if result.get("failed") or "0" not in result.get("success", {}):
            raise RuntimeError(f"Attachment creation failed for {parent_key}: {result}")
        return result["success"]["0"]

    def upload_markdown(self, attachment_key: str, source: Path) -> None:
        data = source.read_bytes()
        response = self._write(
            "POST",
            f"/users/0/items/{attachment_key}/file",
            data={"md5": hashlib.md5(data).hexdigest(), "filename": source.name, "filesize": str(len(data)), "mtime": str(int(source.stat().st_mtime * 1000))},
        )
        response.raise_for_status()
        upload = response.json()
        if upload.get("exists") == 1:
            return
        binary = requests.post(
            upload["url"],
            headers={"Content-Type": upload.get("contentType") or "text/markdown"},
            data=upload.get("prefix", "").encode("utf-8") + data + upload.get("suffix", "").encode("utf-8"),
            timeout=180,
        )
        if binary.status_code != 201:
            raise RuntimeError(f"File upload failed for {attachment_key}: HTTP {binary.status_code}")
        registered = self._write("POST", f"/users/0/items/{attachment_key}/file", data={"upload": upload["uploadKey"]})
        if registered.status_code != 204:
            raise RuntimeError(f"File registration failed for {attachment_key}: HTTP {registered.status_code}")

    def attachment_path(self, attachment_key: str) -> Path:
        response = self.session.get(f"{self.api_base}/users/0/items/{attachment_key}/file", allow_redirects=False, timeout=60)
        if response.status_code != 302:
            raise RuntimeError(f"Attachment file is unavailable for {attachment_key}: HTTP {response.status_code}")
        return Path(unquote(urlsplit(response.headers["Location"]).path.lstrip("/")))


def copy_sidecars(entry: Entry, destination_markdown: Path) -> int:
    copied = 0
    destination_dir = destination_markdown.parent.resolve()
    for source in referenced_local_images(entry):
        relative = source.relative_to(entry.asset_root)
        target = (destination_dir / relative).resolve()
        if destination_dir not in target.parents:
            raise RuntimeError(f"Unsafe target path for {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != source.read_bytes():
            target.write_bytes(source.read_bytes())
            copied += 1
    return copied


def verify_entry(api: ZoteroLocalApi, parent_key: str, attachment_key: str, entry: Entry, collection_key: str | None) -> list[str]:
    errors: list[str] = []
    parent = api.get_item(parent_key)
    if parent.get("url") != entry.url:
        errors.append("parent URL mismatch")
    if parent.get("itemType") != entry.item_type:
        errors.append("parent item type mismatch")
    if collection_key and collection_key not in parent.get("collections", []):
        errors.append("parent is not in target collection")
    attachment = api.get_item(attachment_key)
    if attachment.get("parentItem") != parent_key:
        errors.append("attachment parent mismatch")
    if attachment.get("linkMode") != "imported_file" or attachment.get("contentType") != "text/markdown":
        errors.append("attachment mode or MIME type mismatch")
    stored_markdown = api.attachment_path(attachment_key)
    if not stored_markdown.is_file() or stored_markdown.stat().st_size <= 0:
        errors.append("stored Markdown is missing or empty")
    for source in referenced_local_images(entry):
        relative = source.relative_to(entry.asset_root)
        if not (stored_markdown.parent / relative).is_file():
            errors.append(f"missing copied image: {relative.as_posix()}")
            break
    return errors


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    collection_key, entries = load_entries(manifest_path)
    report_path = args.report.resolve() if args.report else manifest_path.with_name("zotero-ingestion-report.json")

    source_images = sum(len(referenced_local_images(entry)) for entry in entries)
    if args.dry_run:
        print(json.dumps({"dry_run": True, "entries": len(entries), "collection_key": collection_key, "referenced_images": source_images}, ensure_ascii=False, indent=2))
        return 0

    api = ZoteroLocalApi(args.api_base, args.app_name)
    existing_by_url = api.all_regular_items()
    results = []
    failures = []
    copied_images = 0
    for entry in entries:
        try:
            existing = existing_by_url.get(entry.url)
            if existing:
                parent_key = existing["key"]
                api.update_parent(existing, entry, collection_key, args.update_existing_metadata, args.migrate_item_types)
                parent_action = "reused"
            else:
                parent_key = api.create_parent(entry, collection_key)
                parent_action = "created"
            attachment_key = api.find_markdown_attachment(parent_key, entry.attachment_title)
            attachment_action = "reused"
            if not attachment_key:
                attachment_key = api.create_attachment(parent_key, entry)
                attachment_action = "created"
            api.upload_markdown(attachment_key, entry.markdown_path)
            copied = copy_sidecars(entry, api.attachment_path(attachment_key))
            copied_images += copied
            errors = verify_entry(api, parent_key, attachment_key, entry, collection_key)
            result = {"url": entry.url, "parent_key": parent_key, "attachment_key": attachment_key, "parent_action": parent_action, "attachment_action": attachment_action, "assets_copied": copied, "errors": errors}
            results.append(result)
            if errors:
                failures.append(result)
        except Exception as exc:
            failures.append({"url": entry.url, "error": str(exc)})

    payload = {"entries": len(entries), "assets_copied": copied_images, "results": results, "failures": failures}
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"entries": len(entries), "assets_copied": copied_images, "failures": len(failures), "report": str(report_path)}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
