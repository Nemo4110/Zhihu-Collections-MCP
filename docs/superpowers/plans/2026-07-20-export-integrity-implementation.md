# Export Integrity Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add balanced integrity verification, strict audit/repair/force modes, atomic persistence, collection count reconciliation, and machine-readable manifests/reports to the Zhihu exporter.

**Architecture:** Introduce a pure `integrity.py` module for models, hashing, normalization, validation, manifests, policies, and atomic writes. Keep HTTP and orchestration in `main.py`, but make network helpers injectable enough for deterministic `unittest` coverage. Preserve existing public functions used by `mcp_server.py` while routing CLI exports through the new integrity pipeline.

**Tech Stack:** Python 3.13, standard-library `argparse`, `dataclasses`, `hashlib`, `json`, `tempfile`, `unittest`; existing `requests`, `beautifulsoup4`, and `markdownify`.

---

### Task 1: Integrity models, hashing, and atomic writes

**Files:**
- Create: `integrity.py`
- Create: `test/test_integrity_core.py`

- [ ] **Step 1: Write failing core tests**

```python
class IntegrityCoreTests(unittest.TestCase):
    def test_canonicalize_answer_url_removes_query(self):
        self.assertEqual(
            canonicalize_url("https://www.zhihu.com/question/1/answer/2?utm=x"),
            "https://www.zhihu.com/question/1/answer/2",
        )

    def test_parse_source_identity(self):
        self.assertEqual(parse_source_identity("https://zhuanlan.zhihu.com/p/99"), ("article", "99"))

    def test_atomic_write_text_replaces_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.md"
            path.write_text("old", encoding="utf-8")
            atomic_write_text(path, "new")
            self.assertEqual(path.read_text(encoding="utf-8"), "new")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
.venv/Scripts/python.exe -m unittest test.test_integrity_core -v
```

Expected: import failure because `integrity.py` does not exist.

- [ ] **Step 3: Implement models and primitives**

Implement in `integrity.py`:

```python
SCHEMA_VERSION = 1
TEXT_COVERAGE_THRESHOLD = 0.98

@dataclass(frozen=True)
class SourceMetadata:
    canonical_url: str
    source_type: str
    source_id: str
    updated_time: int | None = None

@dataclass(frozen=True)
class SourceSnapshot:
    metadata: SourceMetadata
    candidate: str
    html: str
    text_segments: tuple[str, ...]
    image_urls: tuple[str, ...]

@dataclass(frozen=True)
class IntegrityIssue:
    code: str
    message: str

@dataclass
class IntegrityResult:
    valid: bool
    status: str
    text_coverage: float
    issues: list[IntegrityIssue] = field(default_factory=list)
    assets: list[dict] = field(default_factory=list)
```

Implement `canonicalize_url`, `parse_source_identity`, `sha256_bytes`, `sha256_text`, `sha256_file`, `atomic_write_text`, and `atomic_write_json`. Atomic writers create a named temporary file in the destination directory, flush, call `os.fsync`, and use `os.replace`; cleanup occurs in `finally`.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
.venv/Scripts/python.exe -m unittest test.test_integrity_core -v
```

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit**

```bash
git add integrity.py test/test_integrity_core.py
git commit -m "feat: add integrity models and atomic writes"
```

### Task 2: Text and image completeness validation

**Files:**
- Modify: `integrity.py`
- Create: `test/test_integrity_validation.py`

- [ ] **Step 1: Write failing validation tests**

Tests cover:

```python
def test_missing_last_paragraph_fails_coverage():
    snapshot = snapshot_from_html(
        metadata,
        "api",
        "<h2>Start</h2><p>Middle paragraph with enough text.</p><p>Final required paragraph.</p>",
    )
    result = validate_markdown(snapshot, "> URL\n## Start\nMiddle paragraph with enough text.", assets_dir)
    assert not result.valid
    assert "missing_last_segment" in [issue.code for issue in result.issues]


def test_complete_markdown_passes():
    snapshot = snapshot_from_html(metadata, "api", SOURCE_HTML)
    result = validate_markdown(snapshot, COMPLETE_MARKDOWN, assets_dir)
    assert result.valid
    assert result.text_coverage == 1.0


def test_missing_image_fails():
    snapshot = snapshot_from_html(metadata, "api", '<p>Text body long enough.</p><img src="https://pic.zhimg.com/a.jpg">')
    result = validate_markdown(snapshot, '> URL\nText body long enough.\n![[a.jpg]]', assets_dir)
    assert "missing_asset" in [issue.code for issue in result.issues]
```

- [ ] **Step 2: Run tests and verify RED**

```bash
.venv/Scripts/python.exe -m unittest test.test_integrity_validation -v
```

Expected: missing validation functions.

- [ ] **Step 3: Implement normalization and validation**

Add:

- `normalize_visible_text`
- `extract_text_segments`
- `extract_image_urls`
- `image_filename_from_url`
- `snapshot_from_html`
- `markdown_visible_text`
- `weighted_text_coverage`
- `validate_markdown`

Validation must check canonical source URL, non-empty file, first/last non-trivial source segments, weighted coverage `>= 0.98`, and every expected asset exists with size greater than zero. Image-only snapshots validate by URL and assets.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
.venv/Scripts/python.exe -m unittest test.test_integrity_validation -v
```

- [ ] **Step 5: Commit**

```bash
git add integrity.py test/test_integrity_validation.py
git commit -m "feat: validate markdown text and image completeness"
```

### Task 3: Manifest storage and decision policy

**Files:**
- Modify: `integrity.py`
- Create: `test/test_integrity_manifest.py`
- Create: `test/test_integrity_policy.py`

- [ ] **Step 1: Write failing manifest tests**

Cover valid round-trip, absent manifest, malformed JSON, and rejection of unknown `schema_version` without overwriting the file.

```python
store = IntegrityManifestStore(path, collection_id="1", collection_url="https://www.zhihu.com/collection/1")
store.records[canonical_url] = record
store.save()
loaded = IntegrityManifestStore.load(path, expected_collection_id="1")
self.assertEqual(loaded.records[canonical_url]["status"], "verified")
```

- [ ] **Step 2: Write failing policy tests**

Define `ExportMode` and `decide_action` tests for:

- missing file -> `FETCH_AND_WRITE`;
- file without manifest -> `FETCH_AND_VERIFY`;
- verified file, matching local hash/assets and unchanged metadata -> `SKIP_VERIFIED`;
- changed `updated_time` -> `FETCH_AND_WRITE`;
- audit -> `FETCH_AND_AUDIT`;
- audit+repair invalid -> `FETCH_AND_WRITE`;
- force -> `FETCH_AND_WRITE`.

- [ ] **Step 3: Run tests and verify RED**

```bash
.venv/Scripts/python.exe -m unittest test.test_integrity_manifest test.test_integrity_policy -v
```

- [ ] **Step 4: Implement manifest and policy**

Add `IntegrityManifestStore`, `ManifestSchemaError`, `ExportMode`, `IntegrityAction`, `local_record_is_intact`, `decide_action`, and `record_from_validation`. Manifest saves use `atomic_write_json`.

- [ ] **Step 5: Run tests and verify GREEN**

```bash
.venv/Scripts/python.exe -m unittest test.test_integrity_manifest test.test_integrity_policy -v
```

- [ ] **Step 6: Commit**

```bash
git add integrity.py test/test_integrity_manifest.py test/test_integrity_policy.py
git commit -m "feat: add integrity manifest and refresh policy"
```

### Task 4: Complete collection pagination and reconciliation

**Files:**
- Modify: `integrity.py`
- Modify: `main.py`
- Create: `test/test_collection_reconciliation.py`

- [ ] **Step 1: Write failing collection tests**

Use a fake `request_get` callable with response objects implementing `raise_for_status()` and `json()`.

Test:

- 45 items require offsets 0, 20, and 40;
- two `pin` objects are recorded as unsupported while `complete=True`;
- a failed middle page retries three times and returns `complete=False` rather than a partial success;
- duplicate canonical URLs are reconciled explicitly;
- `raw_item_count` must equal `expected_total`.

- [ ] **Step 2: Run tests and verify RED**

```bash
.venv/Scripts/python.exe -m unittest test.test_collection_reconciliation -v
```

- [ ] **Step 3: Implement robust pagination**

Add `CollectionItem`, `UnsupportedItem`, and `CollectionFetchResult` dataclasses to `integrity.py`.

Replace the body of `get_article_urls_in_collection` with a compatibility wrapper over:

```python
def fetch_collection_items(
    collection_id,
    request_get=requests.get,
    sleep=time.sleep,
    attempts=3,
    timeout=30,
) -> CollectionFetchResult:
    expected_total = get_article_nums_of_collection(collection_id)
    result = CollectionFetchResult(collection_id=collection_id, expected_total=expected_total)
    for offset in range(0, expected_total, 20):
        url = f"https://www.zhihu.com/api/v4/collections/{collection_id}/items?offset={offset}&limit=20"
        response = None
        for attempt in range(attempts):
            try:
                response = request_get(url, headers=headers, cookies=cookies, timeout=timeout)
                response.raise_for_status()
                payload = response.json()
                break
            except Exception as exc:
                if attempt == attempts - 1:
                    result.page_failures.append({"offset": offset, "error": str(exc)})
                else:
                    sleep(2 ** attempt)
        if response is None or result.page_failures:
            break
        for raw_item in payload.get("data", []):
            result.add_raw_item(raw_item)
    result.reconcile()
    return result
```

The wrapper still returns `(urls, titles)` for `mcp_server.py`, but CLI orchestration uses the full result and refuses incomplete collections.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
.venv/Scripts/python.exe -m unittest test.test_collection_reconciliation -v
```

- [ ] **Step 5: Commit**

```bash
git add integrity.py main.py test/test_collection_reconciliation.py
git commit -m "fix: reject incomplete collection pagination"
```

### Task 5: Source candidates and metadata

**Files:**
- Modify: `integrity.py`
- Modify: `main.py`
- Create: `test/test_source_candidates.py`

- [ ] **Step 1: Write failing candidate-selection tests**

Cover:

- longer authenticated API answer beats shorter page answer;
- page candidate is used when API fails;
- article embedded initial JSON beats a shorter visible container;
- verification/login pages are rejected;
- image-only content is accepted;
- answer metadata exposes `updated_time` without logging cookies.

- [ ] **Step 2: Run tests and verify RED**

```bash
.venv/Scripts/python.exe -m unittest test.test_source_candidates -v
```

- [ ] **Step 3: Implement source candidate pipeline**

Add pure `choose_best_snapshot(candidates)` to `integrity.py`.

Add network helpers in `main.py`:

```python
def fetch_answer_metadata(answer_url, request_get=requests.get) -> SourceMetadata:
    source_type, answer_id = parse_source_identity(answer_url)
    response = request_get(
        f"https://www.zhihu.com/api/v4/answers/{answer_id}?include=updated_time",
        headers=api_headers,
        cookies=cookies,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return SourceMetadata(canonicalize_url(answer_url), source_type, answer_id, payload.get("updated_time"))


def fetch_answer_snapshots(answer_url, request_get=requests.get) -> list[SourceSnapshot]:
    metadata = fetch_answer_metadata(answer_url, request_get=request_get)
    snapshots = []
    api_response = request_get(
        f"https://www.zhihu.com/api/v4/answers/{metadata.source_id}?include=content,updated_time",
        headers=api_headers,
        cookies=cookies,
        timeout=30,
    )
    if api_response.ok and api_response.json().get("content"):
        snapshots.append(snapshot_from_html(metadata, "answer_api", api_response.json()["content"]))
    page_response = request_get(answer_url, headers=headers, cookies=cookies, timeout=30)
    if page_response.ok:
        snapshots.extend(parse_answer_page_candidates(metadata, page_response.text))
    return snapshots


def fetch_article_snapshots(article_url, request_get=requests.get) -> list[SourceSnapshot]:
    source_type, article_id = parse_source_identity(article_url)
    metadata = SourceMetadata(canonicalize_url(article_url), source_type, article_id, None)
    response = request_get(article_url, headers=headers, cookies=cookies, timeout=30)
    response.raise_for_status()
    return parse_article_page_candidates(metadata, response.text)


def fetch_source_snapshot(url, request_get=requests.get) -> SourceSnapshot:
    source_type, _ = parse_source_identity(url)
    candidates = (
        fetch_answer_snapshots(url, request_get=request_get)
        if source_type == "answer"
        else fetch_article_snapshots(url, request_get=request_get)
    )
    return choose_best_snapshot(candidates)
```

All requests use explicit timeouts. Answer full fetch considers API and page candidates. Article fetch considers known containers and `script#js-initialData` content. Existing `get_single_answer_content` and `get_single_post_content` remain compatibility wrappers returning BeautifulSoup content built from the selected snapshot.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
.venv/Scripts/python.exe -m unittest test.test_source_candidates -v
```

- [ ] **Step 5: Commit**

```bash
git add integrity.py main.py test/test_source_candidates.py
git commit -m "feat: select the most complete source candidate"
```

### Task 6: Integrity-aware export pipeline

**Files:**
- Modify: `main.py`
- Modify: `integrity.py`
- Create: `test/test_export_pipeline.py`

- [ ] **Step 1: Write failing pipeline tests**

Use temporary output directories and injected source fetchers to prove:

- an existing valid file without a manifest is adopted without rewrite;
- an invalid existing file is repaired in balanced mode;
- audit-only reports invalid content without changing file bytes;
- audit+repair replaces invalid content;
- force replaces valid content;
- Markdown is not replaced when temporary validation fails;
- manifest record is written only after final validation passes.

- [ ] **Step 2: Run tests and verify RED**

```bash
.venv/Scripts/python.exe -m unittest test.test_export_pipeline -v
```

- [ ] **Step 3: Implement pipeline**

Add `export_collection_with_integrity` and `export_item_with_integrity` orchestration. Reuse the existing `ObsidianStyleConverter`, but use the shared `image_filename_from_url` helper. Generate Markdown in memory, validate it, atomically replace the destination, re-read and validate the final file, then update the manifest.

Keep `process_single_collection` as a compatibility wrapper using balanced mode.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
.venv/Scripts/python.exe -m unittest test.test_export_pipeline -v
```

- [ ] **Step 5: Commit**

```bash
git add integrity.py main.py test/test_export_pipeline.py
git commit -m "feat: export and repair verified artifacts atomically"
```

### Task 7: CLI modes, reports, and exit codes

**Files:**
- Modify: `main.py`
- Modify: `integrity.py`
- Create: `test/test_integrity_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Test `parse_args` and aggregation:

```python
self.assertEqual(parse_args([]).mode, ExportMode.BALANCED)
self.assertEqual(parse_args(["--audit"]).mode, ExportMode.AUDIT)
self.assertEqual(parse_args(["--audit", "--repair"]).mode, ExportMode.AUDIT_REPAIR)
self.assertEqual(parse_args(["--force"]).mode, ExportMode.FORCE)
```

Assert `--repair` alone and `--audit --force` raise `SystemExit(2)`. Test exit `0` for verified supported items with unsupported pins, `1` for item validation failures, and `2` for incomplete collections/schema/config failures.

- [ ] **Step 2: Run tests and verify RED**

```bash
.venv/Scripts/python.exe -m unittest test.test_integrity_cli -v
```

- [ ] **Step 3: Implement CLI/reporting**

Add `argparse`, `RunReport`, atomic `integrity_YYYYMMDD_HHMMSS.json` output, console summary, and `raise SystemExit(main())`.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
.venv/Scripts/python.exe -m unittest test.test_integrity_cli -v
```

- [ ] **Step 5: Commit**

```bash
git add integrity.py main.py test/test_integrity_cli.py
git commit -m "feat: add audit repair and force CLI modes"
```

### Task 8: Documentation and complete verification

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `requirements.txt` only if implementation adds a runtime dependency; otherwise leave unchanged.

- [ ] **Step 1: Document modes and manifest behavior**

Document balanced, audit, audit+repair, and force commands; exit codes; manifest location; migration behavior; unsupported pins; and security expectations.

- [ ] **Step 2: Run the complete deterministic suite**

```bash
.venv/Scripts/python.exe -m unittest discover -s test -p "test_*.py" -v
.venv/Scripts/python.exe -m compileall -q main.py integrity.py mcp_server.py fetch_collections.py get_collections.py utils.py test
.venv/Scripts/python.exe -m pip check
```

Expected: zero failures and compatible packages.

- [ ] **Step 3: Run live smoke tests without copying credentials into Git**

Use the ignored cookie file from the primary checkout only for the process lifetime. Run one balanced pass and one audit pass against the configured collections. Verify:

- collection reconciliation succeeds;
- manifests are created;
- supported items verify;
- unsupported pins are reported;
- audit produces no false truncation for answer `1944730534975629202`;
- no cookie value appears in logs or reports.

- [ ] **Step 4: Inspect Git diff and secret safety**

```bash
git status --short
git diff --check
git grep -n "z_c0\|SESSIONID" -- ':!README.md' ':!CLAUDE.md'
git ls-files cookies.json
```

Expected: no tracked cookie file or credential value.

- [ ] **Step 5: Commit documentation and final adjustments**

```bash
git add README.md CLAUDE.md requirements.txt main.py integrity.py test
git commit -m "docs: document export integrity workflow"
```

- [ ] **Step 6: Final verification and branch report**

Record branch name, worktree path, commit list, test counts, live audit totals, and any known limitations. Keep the worktree and branch for user review; do not merge or push without an explicit request.
