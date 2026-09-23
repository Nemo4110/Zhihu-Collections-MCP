# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 重要规则
- **始终使用中文回答**
- 在处理此项目时，所有交流都应使用中文

## 项目概述

Export-Zhihu-Collections 是一个将知乎收藏夹导出为 Markdown 格式的 Python 工具。该工具支持公开和私密收藏夹，可批量处理多个收藏夹，支持自定义输出路径，可下载内嵌图片，并将 HTML 内容转换为 Obsidian 兼容的 Markdown 格式。

## 核心组件

### 主要架构
- **main.py**: 包含收藏夹导出逻辑的主脚本
  - `load_config()`: 加载和解析配置文件
  - `parse_output_path()`: 跨平台路径解析和处理
  - `get_article_urls_in_collection()`: 使用知乎 API 获取收藏夹中的所有 URL
  - `get_single_answer_content()`: 从问答页面提取回答内容（增强版，支持多种页面结构）
  - `get_single_post_content()`: 从专栏文章提取内容（增强版，支持多种页面结构）
  - `process_single_collection()`: 处理单个收藏夹的完整流程
  - `flush_logs()`: 实时日志刷新功能
  - `setup_debug_logging()`: 调试日志配置
  - `get_debug_path()`: 获取调试文件保存路径
  - `ObsidianStyleConverter`: 用于 Obsidian 风格输出的自定义 MarkdownConverter
- **fetch_collections.py**: 独立的收藏夹获取脚本
  - `get_collections_from_page()`: 从知乎页面解析收藏夹信息
  - `update_config_with_collections()`: 自动更新配置文件
- **utils.py**: 包含用于文件名清理的 `filter_title_str()` 函数
- **integrity.py**: 导出完整性核心模块，负责规范 URL、源内容/Markdown 哈希、公式与图片的对称文本归一化、分段相似度覆盖率、警告分级、图片清单、原子写入、完整性清单、刷新策略和运行报告
- **config.json**: 主配置文件，包含收藏夹列表、输出路径和系统设置
- **config_examples.json**: 各种操作系统的配置示例
- **zhihuUrls.json**: 旧版收藏夹URL列表文件（向后兼容）
- **cookies.json**: 访问私密收藏夹的认证 cookies
- **test/**: 测试文件目录，包含各种功能测试脚本

### 数据流程
1. 加载配置文件 (config.json 或 zhihuUrls.json)
2. 解析和验证输出路径，支持多种操作系统格式
3. 批量处理多个收藏夹：
   - 解析收藏夹 URL 提取收藏夹 ID
   - 使用知乎 API 获取收藏夹中的所有项目（分页处理，使用 offset/limit）
   - 对每个项目，判断类型（回答或文章）并获取内容
   - 检查文件是否已存在，避免重复下载
   - 使用自定义转换器将 HTML 转换为 Markdown
   - 下载并保存图片到 assets 文件夹
   - 使用清理后的文件名保存 Markdown 文件
4. 生成详细的处理日志并保存到 logs 目录

## 常用命令

### 安装和设置
```bash
# 安装依赖
pip install -r requirements.txt
```


### 完整性模式
```bash
# 平衡模式：只刷新远端更新、本地异常或尚未建立清单的内容
python main.py

# 严格只读审计
python main.py --audit

# 严格审计并自动修复
python main.py --audit --repair

# 强制刷新全部支持内容
python main.py --force

# 跳过回答网页候选抓取，仅用 API 候选校验（网页请求易被知乎限流 403）
python main.py --no-page-candidates

# 急切抓取网页候选（默认仅当 API 候选失败时才尝试网页候选）
python main.py --page-candidates
```

回答校验默认懒升级：优先使用 API 候选，仅当 API 候选失败/缺失时才抓取网页候选（`--page-candidates` 可恢复急切双候选模式）；网页候选连续失败 5 次（`PAGE_CANDIDATE_FAILURE_LIMIT`，疑似限流累积）后自动熔断，本次运行剩余项目仅用 API 候选校验。进度与日志统一通过 `logging` 输出（不使用 tqdm），格式为 `时间 - 级别 - [文件名:行号] - 消息`，每项内容一行进度（`校验 <收藏夹> [i/N]: 标题`）。

每个收藏夹目录包含 `.zhihu-integrity.json`；每次运行在输出目录的 `logs/` 下生成 `integrity_*.json`。文本覆盖率 `0.90-0.98` 且无硬错误时记录为 `verified_with_warnings`，不导致非零退出码；硬性支持内容验证失败返回 1，配置/分页/清单结构失败返回 2。`pin`/想法会明确报告为不支持类型，但不会导致其他回答和专栏失败。专栏正文优先使用 `zhuanlan.zhihu.com/api/articles/{id}`，失败后回退网页解析；图片下载有三次有界重试并可复用已有非空资源。 收藏夹分页遵循 `paging.next/is_end`，项目总数直接取自第一页响应的 `paging.totals`（缺失时回退独立总数请求）；已到末页但 API 报告总数大于可见项目数时记录 `total_mismatch` 和收藏夹 warning，不重复边界条目。

导出为项级并发（`ITEM_EXPORT_WORKERS`，默认 3），清单读写通过 `_manifest_lock` 互斥并在每项完成后落盘；图片在渲染前按 URL 并发预取（`IMAGE_PREFETCH_WORKERS`，默认 4，公式 URL 转公式不下载），本地已有同名非空资源（zhimg URL 含内容 hash）时直接复用不再请求。

隔离 worktree 可通过 `ZHIHU_COOKIES_FILE` 引用主检出中被忽略的 Cookie 文件，禁止将 Cookie 内容写入代码、测试、日志或 Git。

### 配置和运行
```bash
# 1. 创建配置文件
cp config_examples.json config.json
# 编辑 config.json，添加你的收藏夹信息

# 2. 运行导出工具（批量处理）
python main.py

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
├── main.py              # 主导出脚本
├── fetch_collections.py # 独立的收藏夹获取脚本
├── utils.py             # 工具函数
├── requirements.txt     # Python 依赖
├── config.json          # 主配置文件
├── config_examples.json # 配置示例文件
├── zhihuUrls.json       # 旧版URL列表（向后兼容）
├── cookies.json         # 认证 cookies（可选）
├── test/                # 测试文件目录
│   ├── README.md        # 测试说明文档
│   ├── test_*.py        # 各种功能测试脚本
│   └── __init__.py      # Python 包初始化文件
└── downloads/           # 默认输出目录
    ├── 收藏夹名称1/      # 按收藏夹名称分类的输出目录
    │   ├── *.md         # 导出的 markdown 文件
    │   └── assets/      # 下载的图片
    ├── 收藏夹名称2/
    ├── logs/            # 处理日志文件
    │   ├── debug_*.log  # 调试日志
    │   └── *.json       # 处理结果日志
    └── debug/           # 调试HTML文件
        ├── debug_answer_*.html  # 回答页面调试文件
        └── debug_post_*.html    # 专栏文章调试文件
```

## 认证

对于私密收藏夹，需要创建包含知乎会话 cookies 的 `cookies.json` 文件：
```json
[
  {"name": "cookie_name", "value": "cookie_value"},
  ...
]
```

如果 cookies 不可用，工具会优雅地回退到未认证模式。

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
