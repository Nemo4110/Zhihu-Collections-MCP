# -*- coding: utf-8 -*-
import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from integrity import IntegrityManifestStore, ManifestSchemaError


@contextmanager
def workspace_directory():
    root = Path.cwd() / ".test-tmp"
    root.mkdir(exist_ok=True)
    directory = root / str(uuid.uuid4())
    directory.mkdir()
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class IntegrityManifestTests(unittest.TestCase):
    def test_absent_manifest_creates_empty_store(self):
        with workspace_directory() as directory:
            path = directory / ".zhihu-integrity.json"
            store = IntegrityManifestStore.load(
                path,
                expected_collection_id="1",
                collection_url="https://www.zhihu.com/collection/1",
            )
            self.assertEqual(store.records, {})
            self.assertEqual(store.collection_id, "1")

    def test_manifest_round_trip(self):
        with workspace_directory() as directory:
            path = directory / ".zhihu-integrity.json"
            store = IntegrityManifestStore.load(
                path,
                expected_collection_id="1",
                collection_url="https://www.zhihu.com/collection/1",
            )
            store.records["https://www.zhihu.com/question/1/answer/2"] = {
                "status": "verified",
                "markdown_sha256": "abc",
            }
            store.save()
            loaded = IntegrityManifestStore.load(path, expected_collection_id="1")
            self.assertEqual(
                loaded.records["https://www.zhihu.com/question/1/answer/2"]["status"],
                "verified",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertIn("updated_at", payload)

    def test_unknown_schema_is_rejected_without_overwrite(self):
        with workspace_directory() as directory:
            path = directory / ".zhihu-integrity.json"
            original = '{"schema_version": 99, "collection_id": "1", "items": {}}\n'
            path.write_text(original, encoding="utf-8")
            with self.assertRaises(ManifestSchemaError):
                IntegrityManifestStore.load(path, expected_collection_id="1")
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_malformed_manifest_is_rejected(self):
        with workspace_directory() as directory:
            path = directory / ".zhihu-integrity.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ManifestSchemaError):
                IntegrityManifestStore.load(path, expected_collection_id="1")


if __name__ == "__main__":
    unittest.main()
