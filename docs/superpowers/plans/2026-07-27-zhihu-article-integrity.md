# Zhihu Article Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably synchronize Zhihu articles and prevent formula/image formatting from causing false integrity failures without weakening real truncation and asset checks.

**Architecture:** Keep HTTP/rendering changes in `main.py` and pure normalization/policy changes in `integrity.py`. Add focused unittest regression cases before each production change, then validate the complete project and the live `AGENTS` collection.

**Tech Stack:** Python 3.13, requests, BeautifulSoup/lxml, markdownify, unittest, difflib.

---

### Task 1: Article API metadata and snapshot fallback

**Files:**
- Modify: `test/test_source_candidates.py`
- Modify: `main.py:767-851`

- [ ] Add failing tests proving `fetch_article_metadata()` reads `updated_time`, article snapshots use `https://zhuanlan.zhihu.com/api/articles/{id}` without requesting the page after API success, and fall back to the page when API fails.
- [ ] Run `python -m unittest test.test_source_candidates -v` and confirm failures are caused by missing article API behavior.
- [ ] Implement `_article_api_url()`, `_article_metadata_from_payload()`, `fetch_article_metadata()`, and API-primary/page-fallback `fetch_article_snapshots()`.
- [ ] Update `_default_fetch_metadata()` so article items use `fetch_article_metadata()`.
- [ ] Re-run `python -m unittest test.test_source_candidates test.test_integrity_policy -v` and require zero failures.

### Task 2: Bounded image retry and existing-asset reuse

**Files:**
- Create: `test/test_image_download_retry.py`
- Modify: `main.py:401-457`

- [ ] Add failing tests where the first image request raises `requests.ConnectionError` and the second succeeds, and where all requests fail but a pre-existing non-empty asset is reused.
- [ ] Run `python -m unittest test.test_image_download_retry -v` and confirm the retry tests fail against current one-shot behavior.
- [ ] Implement `_download_image_content()` with three attempts and bounded delays of one and two seconds.
- [ ] Update `ObsidianStyleConverter.convert_img()` to use the helper and retain a non-empty existing asset after exhausted transient failures.
- [ ] Re-run `python -m unittest test.test_image_download_retry -v` and require zero failures.

### Task 3: Symmetric HTML and Markdown text normalization

**Files:**
- Modify: `test/test_integrity_validation.py`
- Modify: `integrity.py:136-250`

- [ ] Add failing tests for inline equation images, Obsidian two-line image blocks with nested LaTeX brackets/parentheses, and ordinary images between text fragments.
- [ ] Run the focused tests and confirm formula/image cases fail for text coverage rather than asset setup.
- [ ] Split source block text at `img` and `br` boundaries while preserving inline text order.
- [ ] Remove standard Markdown images and generated Obsidian image-plus-alt blocks from comparison text while keeping asset verification unchanged.
- [ ] Re-run `python -m unittest test.test_integrity_validation -v` and require zero failures.

### Task 4: Similarity coverage and warning-level validation

**Files:**
- Modify: `test/test_integrity_validation.py`
- Modify: `test/test_export_pipeline.py`
- Modify: `integrity.py:21-54, 252-327, 457-480`
- Modify: `main.py:1332-1423`

- [ ] Add failing tests proving minor segment differences produce coverage between 0.90 and 0.98 and `verified_with_warnings`, while missing middle content below 0.90 and missing final content remain hard failures.
- [ ] Add a pipeline test proving warning-valid content is written and warnings appear in the item result and manifest.
- [ ] Run the focused tests and verify expected failures.
- [ ] Implement per-segment `difflib.SequenceMatcher` coverage, weighted by normalized segment length.
- [ ] Add `TEXT_COVERAGE_WARNING_THRESHOLD`, an `IntegrityResult.warnings` collection, and warning-aware status calculation without weakening hard issues or asset checks.
- [ ] Persist warnings in manifest records and successful item reports.
- [ ] Re-run validation, pipeline, policy, and CLI tests and require zero failures.

### Task 5: Collection paging reconciliation discovered by live acceptance

**Files:**
- Modify: `test/test_collection_reconciliation.py`
- Modify: `test/test_export_pipeline.py`
- Modify: `integrity.py`
- Modify: `main.py`

- [x] Reproduce the `AGENTS` result where `paging.totals=49`, the API returns 48 visible items, and the final page sets `is_end=true`.
- [x] Prove that decrementing offsets duplicates boundary items and does not reveal a missing item.
- [x] Add failing tests for server-provided next offsets, final-page reconciliation, explicit `total_mismatch`, and collection warning status.
- [x] Follow `paging.next/is_end`, retain strict failure for short pages without paging evidence, and report the total mismatch without fabricating content.
- [x] Re-run collection, pipeline, CLI, and full tests.

### Task 6: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md` only if command or architecture documentation is stale.

- [ ] Document article API fallback, image retry/reuse, warning-level integrity results, and the distinction between warnings and exit-code failures.
- [ ] Run `python -m unittest discover -s test -p "test_*.py"` and require all tests to pass.
- [ ] Run `python -m py_compile main.py integrity.py mcp_server.py` and require exit code 0.
- [ ] Review `git diff --check` and the complete diff for unrelated edits, secret exposure, and compatibility regressions.
- [ ] Do not commit: repository rules require separate explicit authorization for `git commit`.

### Task 7: Live AGENTS collection acceptance

**Files:**
- Runtime-only ignored files under `.test-tmp/` and `output/`; no tracked source changes.

- [ ] Build a temporary config containing only the existing `AGENTS` collection URL and an output directory inside `.test-tmp/`.
- [ ] Set `ZHIHU_COOKIES_FILE` to the ignored cookie file in the primary checkout without printing its contents.
- [ ] Run balanced synchronization with network access.
- [ ] Inspect the generated integrity report and require collection completeness, zero supported-item hard failures, and only explicitly unsupported item types.
- [ ] Remove temporary config/runner files created for acceptance; retain the report path long enough to summarize evidence.
