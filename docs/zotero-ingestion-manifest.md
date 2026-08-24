# Zotero 导入清单格式

`zotero_ingestion.py` 使用 JSON 清单描述一次可审查的导入。先运行 `--dry-run`，在明确授权写入 Zotero 后再移除该选项。

```json
{
  "collection_key": "ABCDEFGH",
  "entries": [
    {
      "title": "Example article",
      "url": "https://example.org/posts/example",
      "item_type": "blogPost",
      "markdown_path": "C:/exports/example.md",
      "asset_root": "C:/exports",
      "fields": {
        "date": "2026-01-15",
        "language": "en",
        "blogTitle": "Example Blog"
      },
      "creators": [
        {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}
      ]
    }
  ]
}
```

## 必填字段

- `title`：新建父条目时使用的标题。
- `url`：规范来源 URL；脚本以完全相同的 URL 在整个 library 中复用父条目。
- `item_type`：预期 Zotero 类型，例如 `blogPost`、`forumPost` 或 `webpage`。
- `markdown_path`：非空 Markdown 文件的绝对路径，或相对于清单文件的路径。

## 可选字段

- `collection_key`：目标 collection。新条目会创建于此；复用条目会增量加入，不移除已有归属。
- `asset_root`：Markdown 中相对图片路径的根目录。默认值是 Markdown 文件所在目录。对 `assets/figure.png`，这里应指定包含 `assets/` 的目录。
- `fields`：适用于 `item_type` 的原始 Zotero 字段，例如 `date`、`language`、`blogTitle` 或 `forumTitle`。
- `creators`：完整 creator 列表。对复用的父条目，只有使用 `--update-existing-metadata` 才会写入。
- `attachment_title`：Markdown 子附件标题；未指定时使用 `Markdown — <文件名>`。

## 幂等性与安全边界

- 清单内 URL 不得重复。
- 每个 Markdown 和其引用的本地图片都必须存在且不为空。
- 图片路径不得逃逸 `asset_root`；复制目标不得逃逸附件的 Zotero storage 目录。
- 匹配到已有 Markdown 附件时，脚本复用该附件而不是新增副本。
- `--migrate-item-types` 和 `--update-existing-metadata` 都是显式覆盖开关。
- 脚本不删除任何附件。旧 HTML 等清理需在验证后单独、明确授权执行。
