# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 重要规则
- **始终使用中文回答**
- 在处理此项目时，所有交流都应使用中文

## 项目概述

Export-Zhihu-Collections 是一个将知乎收藏夹导出为 Markdown 格式的 Python 工具。该工具支持公开和私密收藏夹，可批量处理多个收藏夹，支持自定义输出路径，可下载内嵌图片，并将 HTML 内容转换为 Obsidian 兼容的 Markdown 格式。

## 核心组件

### 主要架构（深模块分层）
- **cli.py**: 命令行装配层（参数解析、配置加载、日志装配、输出根解析），导入零副作用
- **exporter.py**: 导出引擎深模块：`export_item_with_integrity()`（单项 verify/adopt/repair/refresh）、`export_collection_with_integrity()`（收藏夹级并发导出 + 清单落盘，`output_root` 与删除同步安全开关显式注入）、`determine_exit_code()`、`summarize_collection_reports()`（控制台/JSON 共用汇总）、`save_integrity_report()`
- **sources.py**: 知乎源数据抓取层：`fetch_collection_items()`（分页对账）、`fetch_answer_snapshots()` / `fetch_article_snapshots()`（API/网页双候选）、`fetch_source_snapshot()`、网页候选熔断状态
- **render.py**: 渲染模块：`Renderer` / `render_markdown()`（快照 HTML → Obsidian Markdown，公式转 LaTeX）、`prefetch_images()`（并发预取）、`ImageAssetTracker`（单项下载/复用/降级统计）；assets_dir、client 与 tracker 显式注入，无全局状态
- **zhihu_client.py**: 知乎 HTTP 边界适配器：`ZhihuClient`（`get_api` / `get_page` / `get_page_data` / `download`）持有头部策略与重试退避；`default_client()` 惰性加载一次 Cookie；`preflight_authentication()` 在写入前验证登录状态，`AuthenticationError` 穿透重试与候选回退
- **integrity.py**: 导出完整性核心模块（纯逻辑），负责规范 URL、源内容/Markdown 哈希、公式与图片的对称文本归一化、分段相似度覆盖率、警告分级、图片清单、原子写入、完整性清单、刷新策略和运行报告
- **paths_config.py**: 配置与跨平台路径模块：`load_config()` / `parse_output_path()` / `load_cookies()` / `get_current_os()`，路径参数可注入
- **main.py**: 向后兼容的 CLI 入口 shim（激活虚拟环境后 `python main.py` 用法不变）
- **fetch_collections.py**: 独立的收藏夹获取脚本（复用 paths_config）
- **mcp_server.py**: MCP 服务，直接接 exporter/sources 深模块接口（导出走完整性管线）
- **utils.py**: 包含用于文件名清理的 `filter_title_str()` 函数
- **config.json**: 主配置文件，包含收藏夹列表、输出路径和系统设置
- **config_examples.json**: 各种操作系统的配置示例
- **zhihuUrls.json**: 旧版收藏夹URL列表文件（向后兼容）
- **cookies.json**: 访问私密收藏夹的认证 cookies
- **test/**: 测试文件目录，包含各种功能测试脚本

### 数据流程
1. 加载配置文件 (config.json 或 zhihuUrls.json)
2. 解析和验证输出路径，支持多种操作系统格式，只初始化一次日志
3. 通过 `/api/v4/me` 预检显式配置的 Cookie；失效或无法确认时返回 3，不写正文/清单、不归档
4. 批量处理多个收藏夹：
   - 解析收藏夹 URL 提取收藏夹 ID
   - 使用知乎 API 获取收藏夹中的所有项目（分页处理，使用 offset/limit）
   - 对每个项目，判断类型（回答或文章）并获取内容
   - 检查文件是否已存在，避免重复下载
   - 使用自定义转换器将 HTML 转换为 Markdown
   - 下载并保存图片到 assets 文件夹
   - 使用清理后的文件名保存 Markdown 文件
5. 生成详细的处理日志、统一汇总与完整性报告，保存到 logs 目录
## 常用命令

### 安装和设置
```bash
# 推荐：按 pyproject.toml 与 uv.lock 创建项目 .venv
uv sync

# 兼容：普通虚拟环境中安装 requirements.txt
python -m pip install -r requirements.txt
```


### 测试
```bash
uv run python -m unittest discover -s test -p "test_*.py"
```

### 完整性模式
```bash
# 平衡模式：只刷新远端更新、本地异常或尚未建立清单的内容
uv run python main.py

# 严格只读审计
uv run python main.py --audit

# 严格审计并自动修复
uv run python main.py --audit --repair

# 强制刷新全部支持内容
uv run python main.py --force

# 跳过回答网页候选抓取，仅用 API 候选校验（网页请求易被知乎限流 403）
uv run python main.py --no-page-candidates

# 急切抓取网页候选（默认仅当 API 候选失败时才尝试网页候选）
uv run python main.py --page-candidates
```

回答校验默认懒升级：优先使用 API 候选，仅当 API 候选失败/缺失时才抓取网页候选（`--page-candidates` 可恢复急切双候选模式）；网页候选连续失败 5 次（`PAGE_CANDIDATE_FAILURE_LIMIT`，疑似限流累积）后自动熔断，本次运行剩余项目仅用 API 候选校验。进度与日志统一通过 `logging` 输出（不使用 tqdm），格式为 `时间 - 级别 - [文件名:行号] - 消息`，每项内容一行进度（`校验 <收藏夹> [i/N]: 标题`）。

每个收藏夹目录包含 `.zhihu-integrity.json`；每次运行在输出目录的 `logs/` 下生成 `integrity_*.json`，报告 `summary` 与 CLI 控制台统一由 `summarize_collection_reports()` 生成。文本覆盖率 `0.90-0.98` 且无硬错误时记录为 `verified_with_warnings`，不导致非零退出码；硬性支持内容验证失败返回 1，配置/分页/清单结构失败返回 2，Cookie 失效或认证预检无法确认返回 3。`pin`/想法会明确报告为不支持类型，但不会导致其他回答和专栏失败。专栏正文优先使用 `zhuanlan.zhihu.com/api/articles/{id}`，失败后回退网页解析；图片下载有三次有界重试并可复用已有非空资源。 收藏夹分页遵循 `paging.next/is_end`，项目总数直接取自第一页响应的 `paging.totals`（缺失时回退独立总数请求）；已到末页但 API 报告总数大于可见项目数时记录 `total_mismatch` 和收藏夹 warning，不重复边界条目。

导出为项级并发（`ITEM_EXPORT_WORKERS`，默认 3），清单读写通过 `_manifest_lock` 互斥并在每项完成后落盘；认证异常取消尚未开始的任务并停止后续收藏夹。图片在渲染前按 URL 并发预取（`IMAGE_PREFETCH_WORKERS`，默认 4，公式 URL 转公式不下载），本地已有同名非空资源（zhimg URL 含内容 hash）时直接复用不再请求；预取失败仅记 DEBUG，最终远程降级按“文章 + 内容 hash”聚合到报告。

远端删除同步采用归档而非删除本地文件：收藏夹已删除时判定 `not_found`（第一页即 404 且从未取到数据，或分页空列表但元信息 API `/api/v4/collections/{id}` 确认 404——知乎对已删除夹子的 items API 仍返回 200+空；网络错误/5xx/分页中途失败/元信息非 404 均不误判，404 不重试退避），整个收藏夹目录移入输出根的 `.archived.collections/<收藏夹名>/`，状态 `collection_archived` 不计入退出码（cli 汇总打印归档数并回写 config.json 的 `status: archived` 标记，下次运行跳过这些夹子）；分页完整时（包括存在 `total_mismatch` 的完整可见列表），清单中已不在收藏夹列表的条目（如手动取消收藏）其 Markdown 与图片移入 `.archived.items/<收藏夹名>/`（共享图片复制而非移动），清单记录同步移除，归档历史经 `integrity.append_archived_items_manifest` 写入该目录的 `.archived-manifest.json`；`total_mismatch` 仅保留对账 warning，不阻止可逆归档；条目重新出现时重新导出活动副本并保留归档历史；`--audit` 只读模式不动文件仅报告 `archivable_items`；条目仍在列表但正文取不到时仍按 `source_failed`/`total_mismatch` 原有逻辑处理。只有预检确认登录身份的 CLI/MCP 导出才传入 `allow_deletion_sync=True`；匿名模式仅报告 `archivable_items`/`deletion_unconfirmed`，不移动文件。

隔离 worktree 可通过 `ZHIHU_COOKIES_FILE` 引用主检出中被忽略的 Cookie 文件，禁止将 Cookie 内容写入代码、测试、日志或 Git。

### 配置和运行
```bash
# 1. 创建配置文件
cp config_examples.json config.json
# 编辑 config.json，添加你的收藏夹信息

# 2. 运行导出工具（批量处理）
uv run python main.py

# 对于私密收藏夹，确保根目录下存在 cookies.json 文件
```

### 配置文件格式
```json
{
  "zhihuUrls": [
    {
      "name": "收藏夹名称",
      "url": "https://www.zhihu.com/collection/123456789"
    }
  ],
  "outputPath": "自定义输出路径（可选）",
  "os": "操作系统类型（可选，支持自动检测）"
}
```

### 依赖包
主要包：
- `requests`: 向知乎 API 发送 HTTP 请求
- `beautifulsoup4`: HTML 解析
- `markdownify`: HTML 到 Markdown 转换
- `lxml`: XML/HTML 解析器后端

## 文件结构

```
/
├── main.py              # CLI 入口 shim（装配在 cli.py）
├── cli.py               # 命令行装配（参数/配置/日志/输出根，导入零副作用）
├── exporter.py          # 导出引擎（清单/报告/退出码/并发导出）
├── sources.py           # 知乎抓取层（分页对账/双候选/熔断）
├── render.py            # 渲染（HTML→Markdown/公式/图片资产）
├── zhihu_client.py      # 知乎 HTTP 边界适配器
├── paths_config.py      # 配置/跨平台路径/Cookie
├── integrity.py         # 完整性校验核心（纯逻辑）
├── utils.py             # 工具函数
├── mcp_server.py        # MCP 服务（接深模块接口）
├── fetch_collections.py # 独立的收藏夹获取脚本
├── requirements.txt     # 普通 venv/pip 兼容依赖
├── pyproject.toml       # 项目元数据与直接运行依赖
├── uv.lock              # uv 可复现依赖锁
├── config.json          # 主配置文件
├── config_examples.json # 配置示例文件
├── zhihuUrls.json       # 旧版URL列表（向后兼容）
├── cookies.json         # 认证 cookies（可选）
├── scripts/             # 一次性诊断脚本
├── test/                # 测试文件目录（按接缝组织）
└── downloads/           # 默认输出目录
    ├── 收藏夹名称1/      # 按收藏夹名称分类的输出目录
    │   ├── *.md         # 导出的 markdown 文件
    │   └── assets/      # 下载的图片
    ├── 收藏夹名称2/
    └── logs/            # 处理日志文件
```

## 认证

对于私密收藏夹，需要创建包含知乎会话 cookies 的 `cookies.json` 文件：
```json
[
  {"name": "cookie_name", "value": "cookie_value"},
  ...
]
```

只有完全未配置 Cookie 时才使用匿名公开导出；显式配置但失效、格式错误或无法完成预检时停止并返回 3，不静默降级。禁止把 Cookie 名称或值写入日志、清单和报告。

## 功能特性

### 核心功能
- **批量处理**: 支持同时处理多个收藏夹
- **自定义输出**: 支持用户指定输出目录，支持多种操作系统路径格式
- **跨平台兼容**: 支持 Windows、Linux、macOS、FreeBSD、Solaris、Cygwin 等系统
- **智能去重**: 检查已存在文件，避免重复下载
- **详细日志**: 生成处理日志，记录每篇文章的下载状态
- **实时日志**: 支持实时日志刷新，立即显示处理进度
- **自动收藏夹获取**: 通过 `fetch_collections.py` 自动获取用户收藏夹列表

### 内容处理
- 图片使用标准 Markdown 的 `![alt](assets/filename)` 语法下载和引用，知乎公式转换为 LaTeX 数学语法
- 链接卡片转换为带有卡片标题的纯文本
- 引用和脚注经过处理以符合正确的 Markdown 格式
- 文件名经过清理以兼容文件系统
- 每个导出的文件顶部都包含原始 URL 作为引用块
- 处理重复标题文件，自动添加URL ID后缀

### 错误处理和调试
- **健壮的错误处理**: 单个文章失败不影响整体处理流程
- **增强的HTML解析**: 支持多种知乎页面结构变化
- **调试文件生成**: 自动保存无法解析的页面HTML到 `downloads/debug/` 目录
- **详细错误日志**: 记录具体的错误信息和堆栈跟踪
- **自动重试机制**: 对网络错误进行适当的重试
