# -*- coding: utf-8 -*-
import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

import cli
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
        self.assertEqual(cli.parse_args([]).mode, ExportMode.BALANCED)
        self.assertEqual(cli.parse_args(["--audit"]).mode, ExportMode.AUDIT)
        self.assertEqual(
            cli.parse_args(["--audit", "--repair"]).mode,
            ExportMode.AUDIT_REPAIR,
        )
        self.assertEqual(cli.parse_args(["--force"]).mode, ExportMode.FORCE)

    def test_parse_no_page_candidates_flag(self):
        self.assertFalse(cli.parse_args([]).no_page_candidates)
        self.assertTrue(cli.parse_args(["--no-page-candidates"]).no_page_candidates)
        self.assertEqual(
            cli.parse_args(["--no-page-candidates"]).mode,
            ExportMode.BALANCED,
        )

    def test_parse_page_candidates_flags_are_mutually_exclusive(self):
        self.assertFalse(cli.parse_args([]).page_candidates)
        self.assertTrue(cli.parse_args(["--page-candidates"]).page_candidates)
        with self.assertRaises(SystemExit):
            cli.parse_args(["--page-candidates", "--no-page-candidates"])

    def test_invalid_mode_combinations_exit_two(self):
        with self.assertRaises(SystemExit) as repair_only:
            cli.parse_args(["--repair"])
        self.assertEqual(repair_only.exception.code, 2)
        with self.assertRaises(SystemExit) as audit_force:
            cli.parse_args(["--audit", "--force"])
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

    def test_exit_code_three_for_authentication_failure(self):
        reports = [{
            "status": "authentication_failed",
            "collection": {"complete": False},
            "items": [{"status": "source_failed"}],
        }]
        self.assertEqual(exporter.determine_exit_code(reports), 3)

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

    def test_run_summary_reuses_assets_and_groups_image_variants(self):
        first_url = "https://www.zhihu.com/question/1/answer/1"
        second_url = "https://www.zhihu.com/question/1/answer/2"
        reports = [{
            "status": "verified_with_warnings",
            "collection": {"complete": True, "unsupported_items": [{"type": "pin"}]},
            "archived_items": [{"url": "https://www.zhihu.com/question/1/answer/3"}],
            "items": [
                {
                    "name": "甲", "url": first_url, "status": "downloaded",
                    "image_activity": {"downloaded": 1, "reused": 0},
                    "assets": [{
                        "url": "https://pic1.zhimg.com/v2-aaa_720w.jpg",
                        "filename": "v2-aaa_720w.jpg", "status": "verified",
                    }],
                },
                {
                    "name": "乙", "url": second_url, "status": "skipped_verified",
                    "image_activity": {"downloaded": 0, "reused": 1},
                    "assets": [
                        {
                            "url": "https://pic1.zhimg.com/v2-bbb_r.jpg",
                            "filename": "v2-bbb_r.jpg", "status": "remote_fallback",
                        },
                        {
                            "url": "https://pica.zhimg.com/50/v2-bbb_720w.jpg",
                            "filename": "v2-bbb_720w.jpg", "status": "remote_fallback",
                        },
                    ],
                },
            ],
        }]
        summary = exporter.summarize_collection_reports(reports)
        self.assertEqual(summary["downloaded"], 1)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["verified"], 2)
        self.assertEqual(summary["unsupported"], 1)
        self.assertEqual(summary["archived_items"], 1)
        self.assertEqual(summary["images"]["downloaded"], 1)
        self.assertEqual(summary["images"]["reused"], 1)
        self.assertEqual(summary["images"]["verified"], 1)
        self.assertEqual(summary["images"]["remote_fallback"], 1)
        self.assertEqual(len(summary["images"]["issues"]), 1)
        self.assertEqual(len(summary["images"]["issues"][0]["urls"]), 2)

        with workspace_directory() as directory:
            path = exporter.save_integrity_report(
                reports, ExportMode.BALANCED, logs_dir=directory, timestamp="summary"
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"], summary)


if __name__ == "__main__":
    unittest.main()
