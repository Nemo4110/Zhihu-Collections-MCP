# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path

import zotero_ingestion


class ZoteroIngestionManifestTests(unittest.TestCase):
    def write_manifest(self, directory: Path, entry: dict) -> Path:
        manifest_path = directory / "manifest.json"
        manifest_path.write_text(
            json.dumps({"entries": [entry]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return manifest_path

    def test_load_entries_resolves_markdown_and_local_images(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            assets = directory / "assets"
            assets.mkdir()
            image = assets / "figure.png"
            image.write_bytes(b"image")
            markdown = directory / "article.md"
            markdown.write_text("# Article\n\n![](assets/figure.png)\n", encoding="utf-8")
            manifest = self.write_manifest(
                directory,
                {
                    "title": "Article",
                    "url": "https://example.test/article",
                    "item_type": "blogPost",
                    "markdown_path": "article.md",
                    "asset_root": ".",
                },
            )

            collection_key, entries = zotero_ingestion.load_entries(manifest)

            self.assertIsNone(collection_key)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].markdown_path, markdown)
            self.assertEqual(zotero_ingestion.referenced_local_images(entries[0]), [image])

    def test_remote_images_are_not_treated_as_local_sidecars(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            markdown = directory / "article.md"
            markdown.write_text("![](https://example.test/figure.png)\n", encoding="utf-8")
            manifest = self.write_manifest(
                directory,
                {
                    "title": "Article",
                    "url": "https://example.test/article",
                    "item_type": "webpage",
                    "markdown_path": "article.md",
                },
            )

            _, entries = zotero_ingestion.load_entries(manifest)

            self.assertEqual(zotero_ingestion.referenced_local_images(entries[0]), [])

    def test_local_image_path_cannot_escape_asset_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            asset_root = directory / "safe-assets"
            asset_root.mkdir()
            (directory / "outside.png").write_bytes(b"outside")
            markdown = directory / "article.md"
            markdown.write_text("![](../outside.png)\n", encoding="utf-8")
            manifest = self.write_manifest(
                directory,
                {
                    "title": "Article",
                    "url": "https://example.test/article",
                    "item_type": "webpage",
                    "markdown_path": "article.md",
                    "asset_root": "safe-assets",
                },
            )

            _, entries = zotero_ingestion.load_entries(manifest)

            with self.assertRaisesRegex(ValueError, "escapes asset_root"):
                zotero_ingestion.referenced_local_images(entries[0])

    def test_duplicate_urls_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            markdown = directory / "article.md"
            markdown.write_text("content\n", encoding="utf-8")
            manifest = directory / "manifest.json"
            payload = {
                "entries": [
                    {
                        "title": "First",
                        "url": "https://example.test/article",
                        "item_type": "webpage",
                        "markdown_path": "article.md",
                    },
                    {
                        "title": "Second",
                        "url": "https://example.test/article",
                        "item_type": "webpage",
                        "markdown_path": "article.md",
                    },
                ]
            }
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Duplicate URL"):
                zotero_ingestion.load_entries(manifest)


if __name__ == "__main__":
    unittest.main()
