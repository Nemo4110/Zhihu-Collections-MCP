# Zhihu Article Integrity Design

## Goal

Make collection synchronization reliably export Zhihu articles when the public article page is blocked, while distinguishing real Markdown truncation from harmless formula, image-alt, and rich-layout differences.

## Scope

- Add the supported `zhuanlan.zhihu.com/api/articles/{id}` endpoint as the primary article source, with the existing HTML page as a fallback.
- Read article update timestamps from the same API so balanced mode can skip unchanged verified files.
- Retry image downloads with bounded attempts and reuse an existing non-empty asset after transient network failures.
- Normalize source HTML and rendered Markdown symmetrically around images and formulas.
- Replace all-or-nothing segment coverage with length-weighted segment similarity.
- Treat 90%-98% coverage as a warning only when no hard integrity condition fails; keep lower coverage, missing endpoints, empty output, and missing assets as hard failures.
- Follow collection `paging.next/is_end`; preserve an explicit `total_mismatch` warning when the API reaches its final page but reports hidden or stale items in `totals`.
- Add regression tests using minimal synthetic fixtures; use the configured `AGENTS` collection only for final live validation.

## Architecture

`main.py` remains responsible for network acquisition and rendering. Article API payload parsing is isolated in small helpers used by metadata and snapshot acquisition. Image retry remains inside the rendering boundary so callers keep the current API.

`integrity.py` remains a pure validation module. HTML extraction splits text at image and line-break boundaries; Markdown normalization removes generated image blocks from the text comparison while asset validation continues independently. Similarity is computed per short segment and then weighted by normalized segment length.

## Integrity Policy

Hard failures remain non-writable:

- empty Markdown;
- missing canonical source URL;
- substantially missing first or last source segment;
- text coverage below 0.90;
- missing or empty referenced assets.

Coverage from 0.90 up to 0.98 produces `verified_with_warnings`. The item is valid and may be written, but the warning is persisted in the manifest and emitted in the item report. Coverage at or above 0.98 is `verified`.

## Error Handling

- Article API success avoids a blocked page request.
- Empty or failed API responses fall back to the article page parser.
- Image downloads use three attempts with bounded backoff.
- If all image attempts fail but a non-empty existing asset is present, it is retained and rendering continues.
- No authentication values are logged or copied into tests.

## Testing

Unit tests cover API primary/fallback behavior, update metadata, image retry and reuse, formula/image normalization, warning-level coverage, and real truncation. The full existing unittest suite must stay green. Final live acceptance exports only the `AGENTS` collection using the ignored cookie file via `ZHIHU_COOKIES_FILE` and requires a complete collection report with no hard failures for supported items.
