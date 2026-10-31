# -*- coding: utf-8 -*-
"""cli 接缝的行为测试：装配正确、导入无副作用、标志正确传播。

接缝说明：cli 只做装配（参数 → 配置 → 输出根 → 导出引擎），
不含业务逻辑。导出引擎在测试中打桩（系统边界）；配置与输出
目录用临时工作区。导入无副作用用子进程验证（不受套件内其他
模块的导入污染）。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import cli
import sources
from integrity import ExportMode
from paths_config import mark_collections_archived
from zhihu_client import AuthenticationError, AuthenticationStatus


class CliTestCase(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(
            cli,
            "preflight_authentication",
            return_value=AuthenticationStatus(authenticated=True),
        )
        patcher.start()
        self.addCleanup(patcher.stop)


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


VERIFIED_REPORT = {
    "name": "收藏",
    "url": "https://www.zhihu.com/collection/1",
    "status": "verified",
    "collection": {"complete": True, "unsupported_items": []},
    "items": [],
}


ARCHIVED_REPORT = {
    "name": "收藏",
    "url": "https://www.zhihu.com/collection/1",
    "status": "collection_archived",
    "archive_path": None,
    "collection": {"complete": False, "unsupported_items": []},
    "items": [],
}


def write_config(directory, collections=None, output_path=None, open_collection=False):
    config = {
        "zhihuUrls": collections if collections is not None else [
            {"name": "收藏", "url": "https://www.zhihu.com/collection/1"}
        ],
    }
    if output_path:
        config["outputPath"] = output_path
    if open_collection:
        config["openCollection"] = True
    (directory / "config.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )


class ImportPurityTests(unittest.TestCase):
    def test_importing_cli_configures_nothing(self):
        """导入 cli 不得配置日志、不得创建目录（副作用属于 main() 装配阶段）。"""
        code = (
            "import logging, sys; sys.path.insert(0, r'%s'); import cli;"
            "sys.exit(0 if logging.getLogger().handlers == [] else 1)"
            % Path.cwd()
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))


class FlagPropagationTests(CliTestCase):
    def run_cli(self, argv):
        with patch.object(cli, "export_collection_with_integrity", return_value=dict(VERIFIED_REPORT)):
            with patch.object(cli, "save_integrity_report") as save:
                save.return_value = Path("x") / "integrity.json"
                code = cli.main(argv)
        return code

    def test_no_page_candidates_disables_page_candidates(self):
        sources._reset_page_candidate_state(enabled=True, eager=False)
        with workspace_directory() as directory:
            write_config(directory)
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                self.run_cli(["--no-page-candidates"])
                self.assertFalse(sources.PAGE_CANDIDATES_ENABLED)
            finally:
                os.chdir(cwd)

    def test_page_candidates_sets_eager_mode(self):
        sources._reset_page_candidate_state(enabled=True, eager=False)
        with workspace_directory() as directory:
            write_config(directory)
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                self.run_cli(["--page-candidates"])
                self.assertTrue(sources.PAGE_CANDIDATES_EAGER)
            finally:
                os.chdir(cwd)

    def test_flags_reset_between_runs(self):
        sources._reset_page_candidate_state(enabled=True, eager=False)
        with workspace_directory() as directory:
            write_config(directory)
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                self.run_cli(["--no-page-candidates"])
                self.assertFalse(sources.PAGE_CANDIDATES_ENABLED)
                self.run_cli([])
                # 每次运行从参数重建抓取状态，上次运行的关闭不得泄漏
                self.assertTrue(sources.PAGE_CANDIDATES_ENABLED)
            finally:
                os.chdir(cwd)


class AssemblyTests(CliTestCase):
    def test_open_collection_mode_returns_config_error(self):
        with workspace_directory() as directory:
            write_config(directory, open_collection=True)
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                self.assertEqual(cli.main([]), 2)
            finally:
                os.chdir(cwd)

    def test_empty_collections_returns_config_error(self):
        with workspace_directory() as directory:
            write_config(directory, collections=[])
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                self.assertEqual(cli.main([]), 2)
            finally:
                os.chdir(cwd)

    def test_output_root_is_passed_to_exporter(self):
        with workspace_directory() as directory:
            out_root = directory / "自定义根"
            write_config(directory, output_path=str(out_root))
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                with patch.object(
                    cli, "export_collection_with_integrity", return_value=dict(VERIFIED_REPORT)
                ) as export:
                    with patch.object(cli, "save_integrity_report"):
                        cli.main([])
                export.assert_called_once()
                kwargs = export.call_args.kwargs
                self.assertEqual(Path(kwargs["output_root"]), out_root.resolve())
                self.assertEqual(kwargs["mode"], ExportMode.BALANCED)
            finally:
                os.chdir(cwd)


class AuthenticationCliTests(CliTestCase):
    def test_expired_cookie_stops_before_export_and_returns_three(self):
        with workspace_directory() as directory:
            write_config(directory)
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                with patch.object(
                    cli, "preflight_authentication",
                    side_effect=AuthenticationError("Cookie 已失效"),
                ), patch.object(cli, "export_collection_with_integrity") as export, patch.object(
                    cli, "save_integrity_report", return_value=Path("x") / "integrity.json"
                ) as save:
                    code = cli.main([])
                self.assertEqual(code, 3)
                export.assert_not_called()
                report = save.call_args.args[0][0]
                self.assertEqual(report["status"], "authentication_failed")
            finally:
                os.chdir(cwd)

    def test_custom_output_initializes_logging_once(self):
        with workspace_directory() as directory:
            out_root = directory / "输出"
            write_config(directory, output_path=str(out_root))
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                with patch.object(cli, "setup_logging") as setup, patch.object(
                    cli, "export_collection_with_integrity", return_value=dict(VERIFIED_REPORT)
                ), patch.object(cli, "save_integrity_report"):
                    cli.main([])
                setup.assert_called_once_with(out_root.resolve() / "logs")
            finally:
                os.chdir(cwd)

    def test_mid_run_authentication_failure_stops_remaining_collections(self):
        with workspace_directory() as directory:
            write_config(directory, collections=[
                {"name": "甲", "url": "https://www.zhihu.com/collection/1"},
                {"name": "乙", "url": "https://www.zhihu.com/collection/2"},
            ])
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                with patch.object(
                    cli, "export_collection_with_integrity",
                    side_effect=AuthenticationError("HTTP 401"),
                ) as export, patch.object(
                    cli, "save_integrity_report", return_value=Path("x") / "integrity.json"
                ):
                    code = cli.main([])
                self.assertEqual(code, 3)
                self.assertEqual(export.call_count, 1)
                self.assertTrue(export.call_args.kwargs["allow_deletion_sync"])
            finally:
                os.chdir(cwd)


class ArchiveSummaryTests(CliTestCase):
    def test_archived_collections_are_summarized_and_exit_zero(self):
        """远端删除导致的归档要在汇总里可见，且不产生失败退出码。"""
        with workspace_directory() as directory:
            write_config(directory)
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                with patch.object(
                    cli, "export_collection_with_integrity", return_value=dict(ARCHIVED_REPORT)
                ):
                    with patch.object(cli, "save_integrity_report") as save:
                        save.return_value = Path("x") / "integrity.json"
                        buffer = io.StringIO()
                        with redirect_stdout(buffer):
                            code = cli.main([])
                self.assertIn("已归档收藏夹（远端已删除）: 1", buffer.getvalue())
                self.assertEqual(code, 0)
                # 新归档的收藏夹被回写标记到 config.json
                config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
                entry = config["zhihuUrls"][0]
                self.assertEqual(entry["status"], "archived")
                self.assertIn("archivedAt", entry)
            finally:
                os.chdir(cwd)

    def test_marked_collections_are_skipped_without_requests(self):
        """config 已标记 archived 的收藏夹不发请求，报告记 archived_skipped。"""
        with workspace_directory() as directory:
            write_config(directory, collections=[
                {"name": "已删夹", "url": "https://www.zhihu.com/collection/1",
                 "status": "archived", "archivedAt": "2025-01-01T00:00:00+08:00"},
            ])
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                with patch.object(cli, "export_collection_with_integrity") as exporter:
                    with patch.object(cli, "save_integrity_report") as save:
                        save.return_value = Path("x") / "integrity.json"
                        buffer = io.StringIO()
                        with redirect_stdout(buffer):
                            code = cli.main([])
                exporter.assert_not_called()
                self.assertIn("跳过已归档收藏夹（config 已标记）: 1", buffer.getvalue())
                self.assertEqual(code, 0)
            finally:
                os.chdir(cwd)

    def test_summary_without_archives_stays_quiet(self):
        with workspace_directory() as directory:
            write_config(directory)
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                with patch.object(
                    cli, "export_collection_with_integrity", return_value=dict(VERIFIED_REPORT)
                ):
                    with patch.object(cli, "save_integrity_report") as save:
                        save.return_value = Path("x") / "integrity.json"
                        buffer = io.StringIO()
                        with redirect_stdout(buffer):
                            code = cli.main([])
                self.assertNotIn("已归档收藏夹", buffer.getvalue())
                self.assertEqual(code, 0)
            finally:
                os.chdir(cwd)


class MarkCollectionsArchivedTests(unittest.TestCase):
    def _write(self, directory, entries):
        config = {"zhihuUrls": entries, "outputPath": "", "os": ""}
        (directory / "config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

    def test_marks_entries_and_preserves_other_fields(self):
        with workspace_directory() as directory:
            self._write(directory, [
                {"name": "甲", "url": "https://www.zhihu.com/collection/1"},
                {"name": "乙", "url": "https://www.zhihu.com/collection/2"},
            ])
            changed = mark_collections_archived(
                directory / "config.json",
                ["https://www.zhihu.com/collection/1"],
                archived_at="2025-06-01T10:00:00+08:00",
            )
            self.assertTrue(changed)
            config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["zhihuUrls"][0]["status"], "archived")
            self.assertEqual(config["zhihuUrls"][0]["archivedAt"], "2025-06-01T10:00:00+08:00")
            self.assertNotIn("status", config["zhihuUrls"][1])
            # 其余配置字段原样保留
            self.assertEqual(config["outputPath"], "")

    def test_existing_marks_keep_first_timestamp(self):
        with workspace_directory() as directory:
            self._write(directory, [{
                "name": "甲", "url": "https://www.zhihu.com/collection/1",
                "status": "archived", "archivedAt": "2025-01-01T00:00:00+08:00",
            }])
            changed = mark_collections_archived(
                directory / "config.json",
                ["https://www.zhihu.com/collection/1"],
                archived_at="2025-06-01T10:00:00+08:00",
            )
            self.assertFalse(changed)
            config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(
                config["zhihuUrls"][0]["archivedAt"], "2025-01-01T00:00:00+08:00"
            )

    def test_empty_urls_is_noop(self):
        with workspace_directory() as directory:
            self._write(directory, [{"name": "甲", "url": "https://www.zhihu.com/collection/1"}])
            self.assertFalse(mark_collections_archived(directory / "config.json", []))


if __name__ == "__main__":
    unittest.main()
