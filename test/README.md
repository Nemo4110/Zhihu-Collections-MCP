# 测试目录

测试按**接缝**（公共接口）组织，全部为 unittest 测试：

```
.venv/Scripts/python -m unittest discover -s test -p "test_*.py"
```

## 接缝与对应测试

| 模块 | 测试文件 | 说明 |
|---|---|---|
| paths_config | test_paths_config.py | 配置加载/跨平台路径/Cookie |
| zhihu_client | test_zhihu_client.py | HTTP 边界适配器（假传输注入） |
| sources | test_source_candidates.py, test_collection_reconciliation.py | 抓取层（双候选/分页对账/熔断） |
| render | test_render.py, test_image_download_retry.py | 渲染与图片资产 |
| exporter | test_exporter.py, test_export_pipeline.py | 导出引擎（清单/报告/并发） |
| integrity | test_integrity_*.py | 完整性核心（纯逻辑） |
| cli | test_cli.py, test_integrity_cli.py | 装配（导入纯度/标志传播/退出码） |
| mcp_server | test_mcp_server.py | MCP 工具路由（引擎打桩） |
| 其他 | test_markdown_migration.py, test_zotero_ingestion.py, test_fetch_collections.py, test_debug_path.py | 独立脚本功能 |

## 原则

- 断言行为，不断言实现；测试穿过公共接口，不 mock 内部协作者
- 网络只在 zhihu_client 接缝处用假传输替代；其余测试无真实网络
- print 型一次性脚本已随死代码删除（历史见 git log）
