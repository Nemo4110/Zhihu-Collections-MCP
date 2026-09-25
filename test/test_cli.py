# -*- coding: utf-8 -*-
"""cli 接缝的行为测试：装配正确、导入无副作用、标志正确传播。

接缝说明：cli 只做装配（参数 → 配置 → 输出根 → 导出引擎），
不含业务逻辑。导出引擎在测试中打桩（系统边界）；配置与输出
目录用临时工作区。导入无副作用用子进程验证（不受套件内其他
模块的导入污染）。
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import cli
import sources
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


VERIFIED_REPORT = {
    "name": "收藏",
    "url": "https://www.zhihu.com/collection/1",
    "status": "verified",
    "collection": {"complete": True, "unsupported_items": []},
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


class FlagPropagationTests(unittest.TestCase):
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


class AssemblyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
