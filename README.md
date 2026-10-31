# Export-Zhihu-Collections

## 项目介绍
一个功能强大的知乎收藏夹导出工具，支持将知乎收藏夹（公开和私密）批量导出为 Markdown 格式文件。支持自定义输出路径、跨平台兼容、图片下载和 Obsidian 兼容格式。支持使用大模型API一键生成文章总结。

**同时提供 MCP Server**，可被 AI Agent (如 Claude Code) 直接调用，为大模型提供保存知乎收藏夹的能力。

## 主要特性

- 📚 **批量处理**: 支持同时处理多个收藏夹
- 📂 **自定义输出**: 支持用户指定输出目录
- 🖥️ **跨平台兼容**: 支持 Windows、Linux、macOS 等系统
- ✨ **智能去重**: 自动检查已存在文件，避免重复下载
- 🖼️ **图片下载**: 自动下载并保存文章中的图片
- 📝 **Obsidian 兼容**: 输出标准 Markdown 图片链接，并兼容支持 LaTeX 的 Markdown 阅读器
- 📈 **实时日志**: 支持实时日志刷新，立即显示处理进度
- 🔍 **自动收藏夹获取**: 通过独立脚本自动获取用户收藏夹列表
- 🛠️ **健壮错误处理**: 单个文章失败不影响整体处理流程
- 🔧 **增强调试支持**: 自动生成调试文件，便于问题排查
- 🤖 **MCP 支持**: 提供 MCP Server，可被 AI Agent 调用

---

## 两种运行方式

### 方式一：命令行运行（传统方式）

适合直接在终端运行导出任务。

#### 安装

推荐使用 [uv](https://docs.astral.sh/uv/) 创建项目 `.venv` 并按 `uv.lock` 安装依赖，不要向 uv 管理的系统 Python 全局安装：

```bash
uv sync
```

也可以使用普通虚拟环境和 `requirements.txt`：

```bash
python -m venv .venv
# Windows: .venv\Scripts\python.exe -m pip install -r requirements.txt
# Linux/macOS: .venv/bin/python -m pip install -r requirements.txt
```

下文使用 `uv run python`；已激活虚拟环境时，可替换为 `python`。

#### 配置文件设置

首先创建主配置文件：
```bash
# 复制配置示例文件
cp config_examples.json config.json
```

编辑 `config.json` 文件，配置你的收藏夹信息：
```json
{
  "zhihuUrls": [
    {
      "name": "收藏夹名称",
      "url": "https://www.zhihu.com/collection/123456789"
    },
    {
      "name": "另一个收藏夹",
      "url": "https://www.zhihu.com/collection/987654321"
    }
  ],
  "outputPath": "/path/to/your/output/directory",
  "os": "linux"
}
```

其中zhihuUrls的获取可看 ![](./readme/image.png)

#### 自动获取收藏夹（可选）

如果你想自动获取所有收藏夹，可以先运行：
```bash
uv run python fetch_collections.py
```
这将自动更新 `config.json` 文件中的收藏夹列表。

#### 运行主程序
```bash
uv run python main.py
```

---

### 方式二：MCP Server 运行（AI Agent 方式）

适合被 Claude Code 或其他 AI 工具集成调用。

#### 安装 MCP 依赖

运行 `uv sync` 已包含兼容的 MCP 1.x 依赖，无需单独安装。

#### 启动 MCP Server

```bash
uv run python mcp_server.py
```

Server 会通过 stdio 通信，可以在 Claude Code 或其他 MCP 客户端中使用。

#### 可用工具

| 工具名 | 说明 |
|--------|------|
| `list_collections` | 列出配置文件中所有知乎收藏夹 |
| `export_collection` | 导出指定知乎收藏夹为Markdown文件 |
| `get_collection_info` | 获取指定收藏夹的基本信息（文章数量等） |
| `search_collections` | 在配置文件中搜索包含关键词的收藏夹 |

##### list_collections

```python
# 返回格式化的收藏夹列表
```

##### export_collection

参数：
- `collection_url` (必需): 收藏夹URL
- `collection_name` (可选): 收藏夹名称
- `output_dir` (可选): 输出目录

```python
# 示例
export_collection(
    collection_url="https://www.zhihu.com/collection/123456789",
    collection_name="Python学习",
    output_dir="my_downloads"
)
```

##### get_collection_info

参数：
- `collection_url` (必需): 收藏夹URL

##### search_collections

参数：
- `keyword` (必需): 搜索关键词

#### 在 Claude Code 中使用

在项目根目录的 `CLAUDE.md` 或用户配置文件中的 MCP 配置段添加服务器配置：

```json
{
  "mcpServers": {
    "zhihu-collections": {
      "command": "uv",
      "args": ["run", "python", "mcp_server.py"],
      "env": {},
      "cwd": "你的项目路径"
    }
  }
}
```

然后就可以用自然语言调用：

- "列出我的知乎收藏夹"
- "导出收藏夹 https://www.zhihu.com/collection/123456789"
- "搜索包含Python的收藏夹"
- "获取收藏夹 https://www.zhihu.com/collection/123456789 的文章数量"

---

## 配置选项说明

### 基本配置
- **zhihuUrls**: 收藏夹列表，每个收藏夹包含名称和 URL
- **outputPath**: 自定义输出路径（可选，留空使用默认路径）
- **os**: 操作系统类型（可选，留空自动检测）

### 支持的操作系统和路径格式
- **Windows**: `"D:\\Documents\\ZhihuExports"` 或 `"D:/Documents/ZhihuExports"`
- **Linux/Unix**: `"/usr/local/share/zhihu-exports"`
- **macOS**: `"~/Documents/ZhihuExports"`
- **Cygwin**: `"/cygdrive/d/Documents/ZhihuExports"`

### 私密收藏夹访问
对于私密收藏夹，需要创建 `cookies.json` 文件：
```json
[
  {"name": "cookie_name", "value": "cookie_value"},
  {"name": "another_cookie", "value": "another_value"}
]
```

每次 CLI/MCP 网络导出都会先通过 `/api/v4/me` 做认证预检：

- 显式配置了 `cookies.json` 或 `ZHIHU_COOKIES_FILE` 时，Cookie 失效、缺少用户身份或无法完成预检会停止运行，退出码为 `3`，不会写入正文、修改清单或执行归档。
- 认证预检的 `401` 表示登录失效；`403`、网络错误和服务端错误只表示“无法确认”，不会武断报告为 Cookie 过期。普通正文/网页请求的 `403` 仍按限流或权限问题处理。
- 完全未配置 Cookie 时保留匿名公开导出；匿名模式无法区分“收藏夹已删除”和“私密资源不可见”，因此远端删除与疑似移出条目只报告，不归档。
- 日志和运行报告不记录 Cookie 名称或值。


## 导出完整性校验

新版本会在每个收藏夹目录维护 `.zhihu-integrity.json`，记录源正文哈希、知乎更新时间、Markdown 哈希、文本覆盖率和图片状态。现有 Markdown 第一次运行时会重新获取源内容建立基线：验证通过则只写清单，验证失败才修复文件。

### 默认平衡模式

```bash
uv run python main.py
```

默认模式会完整核对收藏夹分页。收藏夹分页响应自带每项内容的更新时间（与回答/专栏 API 同源同值），已有清单且该时间戳未变化、本地哈希及图片均正常的回答会零网络请求快速跳过；缺少清单、源内容已更新、本地文件被修改或图片缺失时会重新验证并按需修复。

### 严格只读审计

```bash
uv run python main.py --audit
```

严格重新获取所有支持内容，检查：

- 收藏夹 API 原始数量是否完整；
- 回答/专栏正文开头、结尾及加权文本覆盖率；
- Markdown 是否包含规范化源 URL；
- Markdown 文件哈希是否与清单一致；
- 原文图片是否均有非空本地文件。

源 HTML 和 Markdown 会使用对称规则忽略公式/图片的展示标记，再按短文本片段计算相似度。覆盖率达到 `0.98` 记为 `verified`；覆盖率在 `0.90` 到 `0.98` 之间且不存在首尾缺失、空正文或资源错误时，记为 `verified_with_warnings` 并写入报告，但不会导致非零退出码；低于 `0.90` 仍是完整性失败。

收藏夹分页遵循 API 返回的 `paging.next` 和 `paging.is_end`。如果 API 已明确到达末页，但 `paging.totals` 大于实际可见项目数，运行会保留 `total_mismatch`（例如隐藏或失效条目）并将收藏夹标为 `verified_with_warnings`，而不是伪造缺失内容或重复边界条目。

审计不覆盖 Markdown。发现支持内容无效时退出码为 `1`，收藏夹分页或清单结构异常时退出码为 `2`。

### 审计并修复

```bash
uv run python main.py --audit --repair
```

只重写严格审计判定无效的文件，并在最终文件再次通过验证后更新清单。

### 强制刷新

```bash
uv run python main.py --force
```

重新下载全部支持的回答和专栏，采用临时文件、`fsync` 和原子替换写入，避免中断时破坏上一份有效 Markdown。

### 远端删除同步（归档）

工具采用“只增不删”的本地模型，远端删除不会直接丢弃本地文件，而是归档：

- **收藏夹被删除**：两种判定信号。其一，收藏夹 API 第一页即返回 404 且从未取到任何数据；其二，分页返回空列表（知乎对已删除夹子的 items API 仍返回 200 + totals=0），此时再请求元信息 API `/api/v4/collections/{id}`，返回 404 判为已删除，返回 200 则是新建空夹，其他错误不判定（网络错误、5xx、分页中途失败都不会误判）。命中时整个收藏夹目录（含 Markdown、图片与 `.zhihu-integrity.json`）移动到输出目录下的 `.archived.collections/<收藏夹名>/`，同名冲突自动加序号后缀；收藏夹状态记为 `collection_archived`，不算失败（退出码 0），并回写 `config.json` 标记 `status: archived`，下次运行直接跳过。重跑 `uv run python fetch_collections.py` 重建列表可清除标记。
- **条目被移出收藏夹（如手动取消收藏）**：仅在收藏夹分页完整时执行。完整性清单中已不在本轮收藏夹列表里的条目，其 Markdown 与引用的图片会移动到 `.archived.items/<收藏夹名>/`（保持 `md + assets/` 相对结构，链接继续有效），清单记录同步移除；被其余保留条目共享的图片复制而非移动。每次归档都会追加到该归档目录的 `.archived-manifest.json`。
- **内容获取不到**（条目仍在列表中但正文已删/被隐藏）：仍按原有逻辑处理，正文抓取失败记 `source_failed`（退出码 1），或仅总数对不上时记 `total_mismatch`（`verified_with_warnings`）。
- **total_mismatch 时延迟归档**：分页对账不上的收藏夹（隐藏/失效条目仍计入 totals）中，疑似移出的条目本轮不归档也不报告，因为无法区分「用户取消收藏」与「暂时不可见」，待 totals 恢复一致后的下一次运行再同步。
- 收藏夹 API 返回 404 时不再重试退避（确定性失败），已删除收藏夹不会拖慢每次运行。
- `--audit` 严格只读模式下不动任何文件，只在报告中列出 `archivable_items` 供人工确认。
- 只有认证预检确认登录身份后才允许实际归档；匿名模式的 404 和疑似移出条目仅报告，避免把权限不足误当删除。

### 完整性报告

每次运行都会在输出目录生成：

```text
logs/integrity_YYYYMMDD_HHMMSS.json
```

报告新增 `summary` 字段，与控制台共用同一汇总逻辑，包含新下载、修复、刷新、未变化、失败、不支持、归档条目，以及图片下载、复用、本地完整和远程降级数量。逐项报告的 `assets` 保留图片 URL、文件名和状态；`summary.images.issues` 按“文章 + 知乎图片内容 hash”归并不同尺寸 URL，避免同一失效图片反复告警。预取阶段的瞬时失败只在 DEBUG 日志记录，最终仍为 `remote_fallback` 时才形成用户级警告。

退出码：

- `0`：所有支持内容验证通过；允许 `verified_with_warnings` 以及已明确报告的 `pin`/想法等暂不支持类型；
- `1`：回答或专栏下载、转换、硬性文本完整性或图片校验失败；
- `2`：配置、命令参数、清单版本或收藏夹分页完整性失败；
- `3`：Cookie 已失效或认证预检无法确认登录状态；为安全起见停止后续导出与归档。

### 在隔离 worktree 中引用 Cookie

无需复制 Cookie 文件，可通过环境变量指定本机被忽略的文件：

```bash
export ZHIHU_COOKIES_FILE=/path/to/cookies.json
uv run python main.py --audit
```

程序只读取 Cookie，不会将 Cookie 名称和值写入完整性清单或报告。

### 当前类型限制

回答和专栏参与完整性验证。知乎 `pin`/想法会作为 `unsupported_items` 写入报告，但当前不会导出，也不会让其他支持内容失败。

## 输出结果

### 文件结构
```
输出目录/
├── 收藏夹名称1/
│   ├── 文章1.md
│   ├── 文章2.md
│   └── assets/
│       ├── image1.jpg
│       └── image2.png
├── 收藏夹名称2/
├── .archived.collections/   # 远端已删除收藏夹的归档目录
│   └── 收藏夹名称3/         # 整目录移入，含 md、assets 与清单
├── .archived.items/         # 被移出收藏夹条目的归档目录
│   └── 收藏夹名称1/
│       ├── 文章3.md
│       ├── .archived-manifest.json
│       └── assets/
├── logs/
│   ├── debug_20240101_120000.log
│   └── 20240101_120000.json
└── debug/
    ├── debug_answer_123456.html
    └── debug_post_789012.html
```

### 默认输出位置
- **默认路径**: 项目目录下的 `downloads/` 文件夹
- **自定义路径**: 在 `config.json` 中指定 `outputPath`
- **图片存储**: 每个收藏夹目录下的 `assets/` 文件夹
- **日志文件**: 输出目录下的 `logs/` 文件夹
- **调试文件**: 输出目录下的 `debug/` 文件夹（保存无法解析的页面HTML）


## 故障排除

### 常见问题
1. **内容下载失败**
   - 查看 `downloads/debug/` 目录中的HTML文件分析页面结构
   - 检查网络连接和cookies是否有效
   - 确认文章URL是否可正常访问

2. **日志文件为空**
   - 程序已修复了日志实时刷新问题
   - 如仍有问题，请检查输出目录的写入权限

3. **TypeError: cannot unpack non-iterable NoneType object**
   - 该问题已在v2.1版本中修复
   - 如仍遇到，请更新到最新版本

4. **专栏文章返回"该文章链接被404"但浏览器能正常打开**
   - v2.1版本新增了智能内容检测和精准错误分析
   - 检查debug目录中的HTML文件了解具体原因
   - 可能需要更新cookies或页面结构发生变化

### 调试支持
- **日志文件**: `downloads/logs/debug_*.log` 包含详细的处理信息
- **调试HTML**: `downloads/debug/debug_*.html` 保存无法解析的页面
- **测试脚本**: `test/` 目录包含各种功能测试脚本

# BUG 反馈
若您在使用过程中遇到任何问题，请在 issue 中提供 BUG 信息。为了方便我复现并解决该问题，请务必附上问题报错的提示或者相关网址。


# 建议
若您有任何建议，欢迎在 issue 中发起讨论

## 更新日志

### v2.3 反爬机制绕过
- 🔄 **API 备用机制**: 回答 HTML 被拦截时使用回答 API；专栏优先使用 `zhuanlan.zhihu.com/api/articles/{id}`，失败后回退网页解析
- 🖼️ **图片重试**: 图片下载最多尝试 3 次；瞬时失败且本地已有非空资源时复用已有文件
- 📦 **流式下载**: 旧版专栏网页抓取仍支持流式下载，解决大文章的网络中断问题
- 🔁 **重试机制**: 专栏文章添加 3 次重试，提高下载成功率
- 🔐 **Headers 增强**: 更新请求 Headers 以模拟现代浏览器，减少被反爬拦截的概率

### v2.2 MCP 支持
- 🤖 **MCP Server**: 新增 `mcp_server.py`，支持被 AI Agent 直接调用
- 📋 **4个工具**: 提供 list_collections / export_collection / get_collection_info / search_collections 工具
- 🔌 **标准协议**: 遵循 MCP 协议，支持 Claude Code 等主流 AI 工具集成

### v2.1 增强功能
- 🚀 **实时日志系统**: 支持实时日志刷新和立即显示处理进度
- 🛠️ **健壮错误处理**: 修复了 TypeError 等关键错误，单个文章失败不影响整体处理
- 🔍 **增强HTML解析**: 支持多种知乎页面结构，提高内容获取成功率
- 🔧 **调试文件生成**: 自动保存无法解析的页面HTML到 `debug/` 目录供分析
- 🔄 **自动收藏夹获取**: 新增 `fetch_collections.py` 独立脚本自动获取收藏夹列表
- 📊 **详细错误日志**: 记录具体错误信息和堆栈跟踪，便于问题定位
- 🎯 **智能内容检测**: 当标准CSS选择器失效时，自动启用智能算法检测文章内容
- 🔍 **精准错误分析**: 区分404、登录要求、权限问题等不同错误类型

### v2.0 新增功能
- ✨ 批量处理多个收藏夹
- ⚙️ 配置文件系统 (config.json)
- 📂 自定义输出路径支持
- 🖥️ 跨平台路径处理
- ✨ 智能文件去重功能
- 📈 详细处理日志系统
- 🔄 向后兼容旧版配置文件

## Todo
- 优化抓取速度
- 增加更多导出格式支持
- GUI 界面开发
- 第三方大模型API支持
