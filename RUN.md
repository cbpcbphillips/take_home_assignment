# Reznar Pipeline Run Guide

This project extracts magic item data from a scanned/image-heavy PDF into a local embedded Postgres database. The CLI lives in `reznar/pipeline.py`; `db.py` starts/connects to the local database; `web.py` can launch pgweb for browsing the output.

## 1. Requirements

You need:

- Python project dependencies managed with `uv`
- An OpenAI API key for real PDF/image extraction and real ontology mapping
- Optional: `pgweb` on your `PATH` if you want browser database viewing

ChatGPT Plus does not cover OpenAI API billing. Your `OPENAI_API_KEY` must be an API key with API billing or credits enabled.

## 2. Install dependencies

```powershell
uv sync
```

Quick import check:

```powershell
uv run python -c "import fitz, PIL, pydantic, psycopg, typer, rich, openai; print('imports ok')"
```

## 3. Create `.env`

Copy the example file:

```powershell
Copy-Item .env.example .env
```

Expected `.env` structure:

```text
OPENAI_API_KEY=your_api_key_here
OPENAI_VISION_MODEL=gpt-4o-mini
OPENAI_MAPPER_MODEL=gpt-4o-mini
REZNAR_PDF_PATH=data/items_combined.pdf
REZNAR_OUTPUT_DIR=data
REZNAR_RENDER_DPI=150
REZNAR_IMAGE_FORMAT=png
REZNAR_MAX_PAGES=
REZNAR_DRY_RUN=false
REZNAR_LOG_LEVEL=INFO
```

Use `REZNAR_RENDER_DPI=150` for the full run. Leave `REZNAR_MAX_PAGES` blank to process all pages. Set `REZNAR_MAX_PAGES=2` only for quick testing. Never commit `.env`.

## 4. Verify the database helper

```powershell
uv run python -c "from db import connect; conn = connect(); print(conn.execute('select 1').fetchone()); conn.close()"
```

This creates or uses the embedded Postgres database under `data/.pg/`.

## 5. Run a quick 2-page test

Real API test:

```powershell
uv run python -m reznar.pipeline clean-artifacts
uv run python -B -c "from reznar.repository import reset_db; reset_db(); print('db reset ok')"
uv run python -m reznar.pipeline run --max-pages 2 --real-api --real-mapper --force-render --force-extract --force-assemble --continue-on-error --request-delay-seconds 3 --mapper-delay-seconds 1 --repair-assembly
uv run python -m reznar.pipeline counts
```

This uses the real OpenAI API, but only processes 2 pages. Run this before spending time and credits on the full PDF.

No-cost mock test:

```powershell
uv run python -m reznar.pipeline run --max-pages 2 --force-render --force-extract --force-assemble --repair-assembly
```

Omitting `--real-api` and `--real-mapper` uses mock/deterministic behavior and does not spend API credits.

## 6. Run the full pipeline

```powershell
uv run python -m reznar.pipeline clean-artifacts
uv run python -B -c "from reznar.repository import reset_db; reset_db(); print('db reset ok')"
uv run python -m reznar.pipeline run --real-api --real-mapper --force-render --force-extract --force-assemble --continue-on-error --request-delay-seconds 3 --mapper-delay-seconds 1 --repair-assembly
uv run python -m reznar.pipeline counts
```

Do not pass `--max-pages` for the full PDF.

- `--real-api` runs vision extraction from rendered page PNGs.
- `--real-mapper` maps assembled item text into related ontology entities.
- `--repair-assembly` merges known multi-page items before validation/loading.
- `--continue-on-error` lets the pipeline preserve successful page outputs even if a page request fails.

## 7. Resume after API failures

If page extraction fails partway through, do not rerun with `--force-extract` unless you intentionally want to pay to redo all pages.

```powershell
uv run python -m reznar.pipeline run --real-api --real-mapper --force-assemble --continue-on-error --request-delay-seconds 5 --mapper-delay-seconds 1 --repair-assembly
```

Existing successful `data/raw_extractions/page_XXX.json` files are reused. Missing failed pages can be retried. Generated error files are under `data/raw_extractions/errors/`.

## 8. Run from cached extraction only

Use this after raw page extraction has already succeeded.

```powershell
uv run python -B -c "from reznar.repository import reset_db; reset_db(); print('db reset ok')"
uv run python -m reznar.pipeline assemble --force --repair-assembly
uv run python -m reznar.pipeline load --repair-assembly --real-mapper --mapper-delay-seconds 1
uv run python -m reznar.pipeline counts
```

This does not rerun vision extraction.

## 9. View the database

CLI counts:

```powershell
uv run python -m reznar.pipeline counts
```

Browser view with pgweb:

```powershell
uv run python web.py --port 8081
```

Open:

```text
http://localhost:8081
```

If `8081` is busy:

```powershell
uv run python web.py --port 8082
```

Useful SQL queries:

```sql
select rarity, count(*)
from magic_items
group by rarity
order by count(*) desc;
```

```sql
select entity_type, count(*)
from item_entities
group by entity_type
order by entity_type;
```

```sql
select name, header_line, source_pages
from magic_items
where cardinality(source_pages) > 1
order by source_pages, name;
```

```sql
select name, header_line, rarity, source_pages, needs_review
from magic_items
order by source_pages, name;
```

## 10. Expected final output

Expected database tables:

- `magic_items` should contain around 80 canonical items.
- `item_entities` should contain related ontology records such as `ItemEffect`, `ItemUsageLimit`, and `ItemChargePool`.
- `validation_errors` should ideally be zero or explainable.
- Multi-page items should be represented as single items with multi-page `source_pages`.

Exact counts may change if prompts or extraction quality are improved.

## 11. Generated files and cleanup

These paths are generated and ignored by Git:

- `data/pages/`
- `data/raw_extractions/`
- `data/assembled/`
- `data/validated/`
- `data/reports/`
- `data/.pg/`

Cleanup commands:

```powershell
uv run python -m reznar.pipeline clean-artifacts
uv run python -m reznar.pipeline clean-db
uv run python -m reznar.pipeline clean-all
```

- `clean-artifacts` removes generated PNG/JSON outputs.
- `clean-db` removes the embedded Postgres files.
- `clean-all` removes both.

Do not use `clean-db` unless you want to reset the whole local database storage.

## 12. Git notes

Commit source code, `RUN.md`, `pyproject.toml`, `uv.lock`, `.env.example`, and relevant project files.

Do not commit:

- `.env`
- generated data folders
- `data/.pg/`
