# Zhihu Export Integrity Design

**Date:** 2026-07-20  
**Branch:** `feature/export-integrity`  
**Base:** `personal/zhihu-export@8a8d1a0`

## 1. Purpose

Add verifiable, resumable integrity guarantees to the Zhihu collection exporter without making every normal run re-download all content. The default path uses a metadata-backed balanced check; a strict audit mode re-fetches every supported source; repair and force modes rebuild invalid or all artifacts.

## 2. Goals

- Detect incomplete collection pagination instead of silently exporting a partial list.
- Detect missing, truncated, locally modified, or stale Markdown files.
- Detect missing or empty downloaded image assets.
- Prefer the most complete available source representation for answers and articles.
- Avoid rewriting verified unchanged files during normal runs.
- Provide strict audit and automatic repair modes.
- Write Markdown and integrity manifests atomically.
- Produce machine-readable reports and meaningful process exit codes.
- Migrate existing exports without deleting or blindly overwriting them.

## 3. Non-goals

- Exporting Zhihu `pin`/想法 content in this change.
- Guaranteeing access to content the authenticated account cannot view.
- Purging or rewriting Git history that previously contained cookies.
- Replacing the existing Markdown style or directory structure.
- Building a GUI.

## 4. User-facing modes

### 4.1 Balanced mode (default)

```bash
python main.py
```

For each supported collection item:

1. Fetch reliable collection pagination and source metadata.
2. If no local file or no manifest entry exists, fetch full source content and establish a verified baseline.
3. If source `updated_time` changed, the local hash changed, an asset is missing, or the previous status is not `verified`, re-fetch and repair the item.
4. Otherwise skip the expensive full-content request.

### 4.2 Strict audit

```bash
python main.py --audit
```

Fetch full source content for every supported item and compare it with local Markdown and assets. Do not modify article files. Write an audit report and exit non-zero when any supported item is invalid or any collection page is incomplete.

### 4.3 Strict audit with repair

```bash
python main.py --audit --repair
```

Run strict audit and atomically regenerate invalid or stale artifacts. Revalidate repaired artifacts before reporting success.

### 4.4 Force refresh

```bash
python main.py --force
```

Fetch and atomically regenerate all supported items regardless of manifest state. Still perform validation and write manifest/report data.

`--repair` without `--audit` is rejected as invalid usage. `--audit` and `--force` are mutually exclusive.

## 5. Architecture

### 5.1 New `integrity.py`

Owns deterministic, side-effect-light integrity behavior:

- URL/source ID parsing and canonicalization.
- SHA-256 hashing for bytes, text, and files.
- Source and Markdown text normalization.
- Weighted text coverage calculation.
- Source image inventory and expected local asset names.
- Manifest load, schema validation, migration, and atomic save.
- Markdown/local asset validation.
- Atomic text writes.
- Integrity report models and JSON serialization.

Core data structures use `dataclasses`:

- `SourceMetadata`
- `SourceSnapshot`
- `LocalArtifact`
- `IntegrityRecord`
- `IntegrityIssue`
- `IntegrityResult`
- `CollectionFetchResult`

### 5.2 Changes in `main.py`

`main.py` remains the orchestration and network layer:

- Parse CLI modes with `argparse`.
- Fetch complete collection listings with retries and count reconciliation.
- Fetch metadata and full source candidates.
- Select the most complete candidate.
- Convert HTML to Markdown using the existing converter.
- Ask `integrity.py` to validate and atomically persist artifacts.
- Save one manifest per collection and one run report under `output/logs/`.
- Return a meaningful exit code from `main()`.

Network operations stay outside `integrity.py` so unit tests can use real deterministic data without network mocks for core validation rules.

## 6. Collection completeness

Replace the current tuple-only pagination result with `CollectionFetchResult` containing:

- `expected_total`
- `raw_item_count`
- `exportable_items`
- `unsupported_items`
- `duplicate_urls`
- `page_failures`
- `complete`

Rules:

1. Every page request uses a 30-second timeout and up to 3 attempts with exponential backoff.
2. A failed page is not treated as a successful partial collection.
3. Each raw API object contributes to exactly one category: exportable, unsupported, malformed, or duplicate.
4. The reconciled count must equal the API-reported total.
5. Unsupported `pin` items are recorded explicitly and do not make the supported export fail.
6. Malformed items, missing pages, or an unreconciled total make the collection incomplete and the process exits non-zero.
7. Canonical URLs prevent the same answer/article from being exported twice.

## 7. Source retrieval and candidate selection

### 7.1 Answers

Use authenticated answer API content as the preferred candidate:

```text
/api/v4/answers/{answer_id}?include=content,updated_time
```

Also retain HTML-page extraction as a fallback/cross-check. An authenticated API response can be longer than an anonymous response, so candidates are never trusted solely by endpoint priority.

### 7.2 Articles

Use the article HTML page and parse all known content locations, including current server-rendered containers and embedded initial JSON when present. Article APIs that return 403 are not required for correctness.

### 7.3 Candidate choice

For every successful candidate:

- Normalize visible text.
- Inventory unique non-data image URLs.
- Reject login, verification, 404, or empty-content pages.

Choose the candidate with the greatest normalized visible-text length. Break ties using image count, then preferred source order. Record candidate source and lengths in the manifest/report.

A candidate containing only images is valid when it has at least one downloadable source image.

## 8. Markdown completeness validation

### 8.1 Normalization

Source normalization:

- Parse HTML with BeautifulSoup.
- Remove `style`, `script`, and non-content SVG placeholders.
- Remove zero-width characters.
- Collapse Unicode whitespace.
- Extract text segments from headings, paragraphs, list items, blockquotes, table cells, and code blocks.

Markdown normalization:

- Remove the leading exported source URL line.
- Preserve visible link text and image alt/caption text while removing Markdown punctuation and destinations.
- Remove zero-width characters and collapse whitespace.

### 8.2 Coverage

Calculate weighted coverage by source segment character count. A segment is covered when its normalized text occurs in normalized Markdown. Segments shorter than 8 characters are combined with adjacent segments before comparison.

A text-bearing artifact is verified when:

- weighted text coverage is at least `0.98`;
- the first non-trivial source segment is present;
- the last non-trivial source segment is present;
- the Markdown contains the exact canonical source URL;
- the Markdown file is non-empty.

Image-only content is verified through source URL presence plus image validation.

The threshold is a named constant and recorded in reports so future schema versions can change it explicitly.

## 9. Image integrity

- Extract unique image URLs from the selected source snapshot.
- Ignore data URLs, avatars outside the selected content node, and removed SVG placeholders.
- Derive filenames with the same canonical helper used by the Markdown converter.
- A required image is valid only when its local file exists and size is greater than zero.
- Record every expected URL, filename, byte size, and status.
- Any missing/empty required image makes the item invalid.
- Balanced mode repairs missing assets even when the Markdown hash matches.
- Audit mode reports without modification unless `--repair` is active.

## 10. Manifest

Each collection directory contains:

```text
.zhihu-integrity.json
```

Schema version 1:

```json
{
  "schema_version": 1,
  "collection_id": "981188948",
  "collection_url": "https://www.zhihu.com/collection/981188948",
  "updated_at": "2026-07-20T00:00:00+08:00",
  "items": {
    "https://www.zhihu.com/question/1/answer/2": {
      "title": "Example",
      "source_type": "answer",
      "source_id": "2",
      "source_updated_time": 1756439890,
      "source_candidate": "answer_api",
      "source_content_sha256": "...",
      "source_text_length": 1707,
      "source_image_count": 1,
      "markdown_path": "Example.md",
      "markdown_sha256": "...",
      "markdown_text_length": 1707,
      "text_coverage": 1.0,
      "assets": [
        {
          "url": "https://pic.example/image.jpg",
          "filename": "image.jpg",
          "size": 12345,
          "status": "verified"
        }
      ],
      "last_verified_at": "2026-07-20T00:00:00+08:00",
      "status": "verified",
      "issues": []
    }
  }
}
```

Unknown schema versions are not overwritten. They produce a manifest error and non-zero exit so incompatible data is not silently destroyed.

Manifest writes use a temporary file in the same directory, flush, `fsync`, and `os.replace`.

## 11. Existing-file migration

When a Markdown file exists but has no valid manifest record:

1. Fetch the full source snapshot once.
2. Validate the existing Markdown and assets.
3. If valid, create the manifest record without rewriting the article.
4. If invalid in balanced mode, repair it.
5. If invalid in audit-only mode, report it without modification.

No existing file is deleted automatically.

## 12. Atomic persistence and interruption recovery

Markdown writes use:

1. Create a temporary file in the target directory.
2. Write UTF-8 content.
3. Flush and `fsync`.
4. Validate the temporary content.
5. Replace the destination with `os.replace`.
6. Update the manifest only after the final file and assets verify successfully.

If interrupted before replacement, the previous verified Markdown remains intact. Stale temporary files are reported and may be safely removed on the next repair/force run.

## 13. Reports and exit codes

Every run writes:

```text
output/logs/integrity_YYYYMMDD_HHMMSS.json
```

The report contains collection reconciliation, item action/status, integrity issues, unsupported item summaries, and totals.

Exit codes:

- `0`: all supported items verified; unsupported items may exist and are reported.
- `1`: one or more supported items failed download, validation, or repair.
- `2`: configuration, manifest schema, CLI usage, or collection completeness failure.

## 14. Security

- Never write cookie names or values to logs, manifests, reports, tests, or exceptions.
- Continue loading `cookies.json` locally and keeping it ignored/untracked.
- Sanitize request errors before report serialization when headers could be present.
- Do not save full authenticated page HTML unless existing debug behavior is explicitly triggered; debug filenames contain IDs, not credentials.

## 15. Test strategy

Use built-in `unittest` and temporary directories.

Unit tests cover:

- URL canonicalization and source ID parsing.
- Hashing and atomic writes.
- Manifest round-trip and unknown-schema rejection.
- Text normalization and weighted coverage, including missing final paragraphs.
- Image URL inventory and missing/empty asset detection.
- Existing-file migration decisions.
- Balanced/audit/repair/force decision matrix.
- Collection count reconciliation and partial-page failure.
- CLI validation and exit-code aggregation.

Integration-style tests use static HTML/API fixtures representing:

- a complete answer API response;
- a shorter page candidate and longer API candidate;
- an article page with initial JSON;
- duplicate titles with different answer IDs;
- image-only content;
- unsupported `pin` items;
- interrupted temporary writes.

No automated test requires live Zhihu access. A final manual smoke test may use the user's local ignored cookie file from the primary checkout without copying it into Git.

## 16. Delivery

Implementation stays on branch `feature/export-integrity` in `.worktrees/export-integrity`. Commits are small and test-backed. The original `personal/zhihu-export` checkout and its uncommitted `config.json` remain untouched.
