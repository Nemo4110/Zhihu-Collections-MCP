# -*- coding: utf-8 -*-
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

import main
import exporter
from integrity import ExportMode


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


class IntegrityCliTests(unittest.TestCase):
    def test_parse_modes(self):
        self.assertEqual(main.parse_args([]).mode, ExportMode.BALANCED)
        self.assertEqual(main.parse_args(["--audit"]).mode, ExportMode.AUDIT)
        self.assertEqual(
            main.parse_args(["--audit", "--repair"]).mode,
            ExportMode.AUDIT_REPAIR,
        )
        self.assertEqual(main.parse_args(["--force"]).mode, ExportMode.FORCE)

    def test_parse_no_page_candidates_flag(self):
        self.assertFalse(main.parse_args([]).no_page_candidates)
        self.assertTrue(main.parse_args(["--no-page-candidates"]).no_page_candidates)
        self.assertEqual(
            main.parse_args(["--no-page-candidates"]).mode,
            ExportMode.BALANCED,
        )

    def test_parse_page_candidates_flags_are_mutually_exclusive(self):
        self.assertFalse(main.parse_args([]).page_candidates)
        self.assertTrue(main.parse_args(["--page-candidates"]).page_candidates)
        with self.assertRaises(SystemExit):
            main.parse_args(["--page-candidates", "--no-page-candidates"])

    def test_invalid_mode_combinations_exit_two(self):
        with self.assertRaises(SystemExit) as repair_only:
            main.parse_args(["--repair"])
        self.assertEqual(repair_only.exception.code, 2)
        with self.assertRaises(SystemExit) as audit_force:
            main.parse_args(["--audit", "--force"])
        self.assertEqual(audit_force.exception.code, 2)

    def test_exit_code_zero_with_verified_and_unsupported_items(self):
        reports = [
            {
                "status": "verified",
                "collection": {"complete": True, "unsupported_items": [{"source_type": "pin"}]},
                "items": [{"status": "audit_verified"}, {"status": "skipped_verified"}],
            }
        ]
        self.assertEqual(exporter.determine_exit_code(reports), 0)

    def test_exit_code_one_for_item_integrity_failure(self):
        reports = [
            {
                "status": "invalid",
                "collection": {"complete": True},
                "items": [{"status": "invalid"}],
            }
        ]
        self.assertEqual(exporter.determine_exit_code(reports), 1)

    def test_exit_code_two_for_incomplete_collection(self):
        reports = [
            {
                "status": "collection_incomplete",
                "collection": {"complete": False},
                "items": [],
            }
        ]
        self.assertEqual(exporter.determine_exit_code(reports), 2)

    def test_integrity_report_is_written_atomically(self):
        with workspace_directory() as directory:
            path = exporter.save_integrity_report(
                [{"status": "verified", "collection": {"complete": True}, "items": []}],
                ExportMode.AUDIT,
                logs_dir=directory,
                timestamp="20260720_010203",
            )
            self.assertEqual(path.name, "integrity_20260720_010203.json")
            text = path.read_text(encoding="utf-8")
            self.assertIn('"mode": "audit"', text)
            self.assertIn('"exit_code": 0', text)


if __name__ == "__main__":
    unittest.main()
