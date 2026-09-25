# -*- coding: utf-8 -*-
"""render 接缝的行为测试：快照 HTML → Obsidian Markdown。

接缝说明：render 模块是纯转换 + 本地资产获取（图片落盘/复用/降级），
网络只通过注入的 client.download。
"""
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from render import Renderer, render_markdown


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


class FakeClient:
    def __init__(self, contents=None, fail=False):
        self.contents = contents or {}
        self.fail = fail
        self.downloaded = []

    def download(self, url, attempts=3):
        self.downloaded.append(url)
        if self.fail:
            raise OSError("404 dead link")
        return self.contents.get(url, b"img-bytes")


EQUATION_TEX = "E=mc^2"


class EquationTests(unittest.TestCase):
    def test_inline_equation_image_becomes_inline_latex(self):
        html = (
            f'<p>质能方程 <img src="https://www.zhihu.com/equation?tex=E%3Dmc%5E2" '
            f'alt="{EQUATION_TEX}"> 很有名</p>'
        )
        with workspace_directory() as directory:
            client = FakeClient()
            markdown = render_markdown(html, directory / "assets", client=client)
            self.assertIn(f"${EQUATION_TEX}$", markdown)
            self.assertNotIn("zhimg", markdown)
            self.assertNotIn("equation", markdown)
            # 公式 URL 转换为 LaTeX，不应触发任何图片下载
            self.assertEqual(client.downloaded, [])

    def test_long_equation_becomes_block_latex(self):
        long_tex = "x=" + "+".join(["1"] * 40)  # > 70 字符
        html = f'<p><img src="https://www.zhihu.com/equation?tex=x%3D1" alt="{long_tex}"></p>'
        with workspace_directory() as directory:
            markdown = render_markdown(html, directory / "assets", client=FakeClient())
            self.assertIn("$$", markdown)
            self.assertIn(long_tex, markdown)


class ImageAssetTests(unittest.TestCase):
    def test_regular_image_downloads_into_assets_and_references_locally(self):
        url = "https://pic1.zhimg.com/v2-abc123_720w.jpg"
        html = f'<p><img src="{url}" alt="示意图"></p>'
        with workspace_directory() as directory:
            client = FakeClient(contents={url: b"jpeg-bytes"})
            markdown = render_markdown(html, directory / "assets", client=client)
            self.assertIn("![示意图](assets/", markdown)
            assets = list((directory / "assets").glob("*"))
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0].read_bytes(), b"jpeg-bytes")
            self.assertIn(assets[0].name, markdown)

    def test_existing_nonempty_asset_is_reused_without_redownload(self):
        url = "https://pic1.zhimg.com/v2-abc123_720w.jpg"
        html = f'<p><img src="{url}"></p>'
        with workspace_directory() as directory:
            client = FakeClient(contents={url: b"new-bytes"})
            # 先渲染一次落盘
            render_markdown(html, directory / "assets", client=client)
            first_count = len(client.downloaded)
            # 再次渲染同图：URL 含内容 hash，本地非空即复用
            render_markdown(html, directory / "assets", client=client)
            self.assertEqual(len(client.downloaded), first_count)

    def test_dead_remote_image_degrades_to_source_url_reference(self):
        url = "https://pic1.zhimg.com/v2-dead404_720w.jpg"
        html = f'<p><img src="{url}" alt="失效图"></p>'
        with workspace_directory() as directory:
            client = FakeClient(fail=True)
            markdown = render_markdown(html, directory / "assets", client=client)
            self.assertIn(f"]( {url})".replace(" ", ""), markdown)
            self.assertIn("失效图", markdown)


class BasicConversionTests(unittest.TestCase):
    def test_headings_use_atx_style(self):
        with workspace_directory() as directory:
            markdown = render_markdown(
                "<h2>小节</h2>", directory / "assets", client=FakeClient()
            )
            self.assertIn("## 小节", markdown)

    def test_renderer_reusable_for_multiple_documents(self):
        with workspace_directory() as directory:
            renderer = Renderer(directory / "assets", client=FakeClient())
            first = renderer.render("<p>第一篇</p>")
            second = renderer.render("<p>第二篇</p>")
            self.assertIn("第一篇", first)
            self.assertIn("第二篇", second)


if __name__ == "__main__":
    unittest.main()
