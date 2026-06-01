"""Postgres persistence layer for the Reznar extraction pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from db import connect


class RepositoryError(Exception):
    """Raised when repository input validation or persistence fails."""


_TABLE_NAMES = [
    "extraction_runs",
    "document_pages",
    "page_extractions_raw",
    "assembled_items_raw",
    "validated_entities",
    "validation_errors",
    "magic_items",
    "item_entities",
]

_DROP_TABLE_ORDER = [
    "item_entities",
    "magic_items",
    "validation_errors",
    "validated_entities",
    "assembled_items_raw",
    "page_extractions_raw",
    "document_pages",
    "extraction_runs",
]

_CREATE_TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS extraction_runs (
        id uuid PRIMARY KEY,
        document_path text,
        model_name text,
        pipeline_version text,
        status text NOT NULL DEFAULT 'running',
        started_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz,
        notes text
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_pages (
        id uuid PRIMARY KEY,
        run_id uuid NULL REFERENCES extraction_runs(id) ON DELETE SET NULL,
        page_number integer NOT NULL,
        image_path text NOT NULL,
        image_sha256 text NOT NULL,
        width integer NOT NULL,
        height integer NOT NULL,
        render_dpi integer NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS page_extractions_raw (
        id uuid PRIMARY KEY,
        run_id uuid NULL REFERENCES extraction_runs(id) ON DELETE SET NULL,
        page_number integer NOT NULL,
        image_path text NOT NULL,
        output_path text,
        raw_data jsonb NOT NULL,
        from_cache boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assembled_items_raw (
        id uuid PRIMARY KEY,
        run_id uuid NULL REFERENCES extraction_runs(id) ON DELETE SET NULL,
        item_index integer NOT NULL,
        name_guess text,
        source_pages integer[] NOT NULL DEFAULT '{}',
        raw_data jsonb NOT NULL,
        needs_review boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS validated_entities (
        id uuid PRIMARY KEY,
        run_id uuid NULL REFERENCES extraction_runs(id) ON DELETE SET NULL,
        entity_type text NOT NULL,
        source_pages integer[] NOT NULL DEFAULT '{}',
        data jsonb NOT NULL,
        needs_review boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS validation_errors (
        id uuid PRIMARY KEY,
        run_id uuid NULL REFERENCES extraction_runs(id) ON DELETE SET NULL,
        entity_type text NOT NULL,
        source_pages integer[] NOT NULL DEFAULT '{}',
        raw_data jsonb NOT NULL,
        errors jsonb NOT NULL,
        needs_review boolean NOT NULL DEFAULT true,
        resolved boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS magic_items (
        id uuid PRIMARY KEY,
        name text NOT NULL,
        header_line text,
        item_category text,
        item_form text,
        subtype text,
        rarity text,
        wear_slot text,
        attunement_required boolean NOT NULL DEFAULT false,
        is_artifact boolean NOT NULL DEFAULT false,
        is_cursed boolean NOT NULL DEFAULT false,
        is_consumable boolean NOT NULL DEFAULT false,
        source_pages integer[] NOT NULL DEFAULT '{}',
        raw_text text,
        data jsonb NOT NULL,
        needs_review boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS item_entities (
        id uuid PRIMARY KEY,
        magic_item_id uuid NULL REFERENCES magic_items(id) ON DELETE CASCADE,
        entity_type text NOT NULL,
        source_pages integer[] NOT NULL DEFAULT '{}',
        data jsonb NOT NULL,
        needs_review boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
]


def init_db() -> None:
    """Create repository tables if they do not already exist."""

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                for statement in _CREATE_TABLES_SQL:
                    cur.execute(statement)
    except Exception as exc:
        raise RepositoryError("Failed to initialize repository tables.") from exc


def reset_db() -> None:
    """Drop and recreate only tables owned by this module."""

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                for table_name in _DROP_TABLE_ORDER:
                    cur.execute(
                        sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table_name))
                    )
    except Exception as exc:
        raise RepositoryError("Failed to reset repository tables.") from exc

    init_db()


def create_extraction_run(
    document_path: str | Path,
    model_name: str | None = None,
    pipeline_version: str | None = None,
    notes: str | None = None,
) -> str:
    """Create a new extraction run and return its ID."""

    run_id = uuid4()
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO extraction_runs (
                        id, document_path, model_name, pipeline_version, notes
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (run_id, _path_text(document_path), model_name, pipeline_version, notes),
                )
    except Exception as exc:
        raise RepositoryError("Failed to create extraction run.") from exc
    return str(run_id)


def finish_extraction_run(
    run_id: str,
    status: str = "completed",
    notes: str | None = None,
) -> None:
    """Mark an extraction run as finished."""

    run_uuid = _uuid_or_error(run_id, "run_id")
    status_text = _non_empty_text(status, "status")
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE extraction_runs
                    SET status = %s,
                        finished_at = now(),
                        notes = COALESCE(%s, notes)
                    WHERE id = %s
                    """,
                    (status_text, notes, run_uuid),
                )
                if cur.rowcount != 1:
                    raise RepositoryError(f"Extraction run not found: {run_id}")
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError(f"Failed to finish extraction run: {run_id}") from exc


def insert_document_page(
    run_id: str | None,
    page_number: int,
    image_path: str | Path,
    image_sha256: str,
    width: int,
    height: int,
    render_dpi: int,
) -> str:
    """Insert one rendered document page row."""

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                return _insert_document_page(
                    cur,
                    run_id=run_id,
                    page_number=page_number,
                    image_path=image_path,
                    image_sha256=image_sha256,
                    width=width,
                    height=height,
                    render_dpi=render_dpi,
                )
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError(f"Failed to insert document page {page_number}.") from exc


def insert_document_pages(run_id: str | None, rendered_pages: object) -> list[str]:
    """Insert rendered PageRenderResult objects and return inserted IDs."""

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                return [
                    _insert_document_page(
                        cur,
                        run_id=run_id,
                        page_number=page.page_number,
                        image_path=page.image_path,
                        image_sha256=page.sha256,
                        width=page.width,
                        height=page.height,
                        render_dpi=page.render_dpi,
                    )
                    for page in rendered_pages
                ]
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError("Failed to insert document pages.") from exc


def insert_page_extraction_raw(
    run_id: str | None,
    page_number: int,
    image_path: str | Path,
    output_path: str | Path | None,
    raw_data: dict[str, object],
    from_cache: bool = False,
) -> str:
    """Insert one raw page extraction row."""

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                return _insert_page_extraction_raw(
                    cur,
                    run_id=run_id,
                    page_number=page_number,
                    image_path=image_path,
                    output_path=output_path,
                    raw_data=raw_data,
                    from_cache=from_cache,
                )
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError(f"Failed to insert raw extraction for page {page_number}.") from exc


def insert_page_extractions_raw(run_id: str | None, page_results: object) -> list[str]:
    """Insert PageExtractionResult objects and return inserted IDs."""

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                return [
                    _insert_page_extraction_raw(
                        cur,
                        run_id=run_id,
                        page_number=result.page_number,
                        image_path=result.image_path,
                        output_path=result.output_path,
                        raw_data=result.data,
                        from_cache=result.from_cache,
                    )
                    for result in page_results
                ]
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError("Failed to insert raw page extractions.") from exc


def insert_assembled_item_raw(
    run_id: str | None,
    item_index: int,
    assembled_item: dict[str, object],
) -> str:
    """Insert one assembled item raw record."""

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                return _insert_assembled_item_raw(
                    cur,
                    run_id=run_id,
                    item_index=item_index,
                    assembled_item=assembled_item,
                )
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError(f"Failed to insert assembled item {item_index}.") from exc


def insert_assembled_items_raw(
    run_id: str | None,
    assembled_items: list[dict[str, object]],
) -> list[str]:
    """Insert assembled item raw records using 1-based item indexes."""

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                return [
                    _insert_assembled_item_raw(
                        cur,
                        run_id=run_id,
                        item_index=index,
                        assembled_item=assembled_item,
                    )
                    for index, assembled_item in enumerate(assembled_items, start=1)
                ]
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError("Failed to insert assembled item raw records.") from exc


def insert_validated_entity(
    run_id: str | None,
    entity_type: str,
    data: dict[str, object],
    source_pages: list[int] | None = None,
    needs_review: bool = False,
) -> str:
    """Insert one validated entity record."""

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                return _insert_validated_entity(
                    cur,
                    run_id=run_id,
                    entity_type=entity_type,
                    data=data,
                    source_pages=source_pages,
                    needs_review=needs_review,
                )
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError(f"Failed to insert validated entity {entity_type}.") from exc


def insert_validation_error(
    run_id: str | None,
    entity_type: str,
    raw_data: dict[str, object],
    errors: list[dict[str, object]],
    source_pages: list[int] | None = None,
    needs_review: bool = True,
) -> str:
    """Insert one validation error record."""

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                return _insert_validation_error(
                    cur,
                    run_id=run_id,
                    entity_type=entity_type,
                    raw_data=raw_data,
                    errors=errors,
                    source_pages=source_pages,
                    needs_review=needs_review,
                )
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError(f"Failed to insert validation error for {entity_type}.") from exc


def insert_validation_result(run_id: str | None, validation_result: object) -> tuple[list[str], list[str]]:
    """Insert all valid entities and validation errors from a ValidationResult-like object."""

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                entity_ids = [
                    _insert_validated_entity(
                        cur,
                        run_id=run_id,
                        entity_type=entity.entity_type,
                        data=entity.data,
                        source_pages=entity.source_pages,
                        needs_review=entity.needs_review,
                    )
                    for entity in validation_result.valid_entities
                ]
                error_ids = [
                    _insert_validation_error(
                        cur,
                        run_id=run_id,
                        entity_type=error.entity_type,
                        raw_data=error.raw_data,
                        errors=error.errors,
                        source_pages=error.source_pages,
                        needs_review=error.needs_review,
                    )
                    for error in validation_result.errors
                ]
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError("Failed to insert validation result.") from exc
    return entity_ids, error_ids


def count_rows(table_name: str) -> int:
    """Return the row count for one known repository table."""

    _require_known_table(table_name)
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table_name))
                )
                row = cur.fetchone()
                if row is None:
                    raise RepositoryError(f"Count query returned no result for {table_name}.")
                return int(row[0])
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError(f"Failed to count rows in {table_name}.") from exc


def list_table_counts() -> dict[str, int]:
    """Return row counts for all known repository tables."""

    return {table_name: count_rows(table_name) for table_name in _TABLE_NAMES}


def _insert_document_page(
    cur: psycopg.Cursor[Any],
    *,
    run_id: str | None,
    page_number: int,
    image_path: str | Path,
    image_sha256: str,
    width: int,
    height: int,
    render_dpi: int,
) -> str:
    _positive_int(page_number, "page_number")
    _positive_int(width, "width")
    _positive_int(height, "height")
    _positive_int(render_dpi, "render_dpi")
    image_sha256 = _non_empty_text(image_sha256, "image_sha256")

    row_id = uuid4()
    cur.execute(
        """
        INSERT INTO document_pages (
            id, run_id, page_number, image_path, image_sha256, width, height, render_dpi
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            _optional_uuid(run_id, "run_id"),
            page_number,
            _path_text(image_path),
            image_sha256,
            width,
            height,
            render_dpi,
        ),
    )
    return str(row_id)


def _insert_page_extraction_raw(
    cur: psycopg.Cursor[Any],
    *,
    run_id: str | None,
    page_number: int,
    image_path: str | Path,
    output_path: str | Path | None,
    raw_data: dict[str, object],
    from_cache: bool,
) -> str:
    _positive_int(page_number, "page_number")
    _require_dict(raw_data, "raw_data")

    row_id = uuid4()
    cur.execute(
        """
        INSERT INTO page_extractions_raw (
            id, run_id, page_number, image_path, output_path, raw_data, from_cache
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            _optional_uuid(run_id, "run_id"),
            page_number,
            _path_text(image_path),
            _optional_path_text(output_path),
            Jsonb(raw_data),
            bool(from_cache),
        ),
    )
    return str(row_id)


def _insert_assembled_item_raw(
    cur: psycopg.Cursor[Any],
    *,
    run_id: str | None,
    item_index: int,
    assembled_item: dict[str, object],
) -> str:
    _positive_int(item_index, "item_index")
    _require_dict(assembled_item, "assembled_item")

    row_id = uuid4()
    cur.execute(
        """
        INSERT INTO assembled_items_raw (
            id, run_id, item_index, name_guess, source_pages, raw_data, needs_review
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            _optional_uuid(run_id, "run_id"),
            item_index,
            _optional_text(assembled_item.get("name_guess")),
            _source_pages(assembled_item.get("source_pages")),
            Jsonb(assembled_item),
            _bool_value(assembled_item.get("needs_review"), default=False),
        ),
    )
    return str(row_id)


def _insert_validated_entity(
    cur: psycopg.Cursor[Any],
    *,
    run_id: str | None,
    entity_type: str,
    data: dict[str, object],
    source_pages: list[int] | None,
    needs_review: bool,
) -> str:
    entity_type = _non_empty_text(entity_type, "entity_type")
    _require_dict(data, "data")

    row_id = uuid4()
    cur.execute(
        """
        INSERT INTO validated_entities (
            id, run_id, entity_type, source_pages, data, needs_review
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            _optional_uuid(run_id, "run_id"),
            entity_type,
            _source_pages(source_pages),
            Jsonb(data),
            bool(needs_review),
        ),
    )

    if entity_type == "MagicItem":
        _insert_magic_item(cur, data=data, row_id=row_id)
    else:
        _insert_item_entity(
            cur,
            entity_type=entity_type,
            data=data,
            source_pages=_source_pages(source_pages),
            needs_review=bool(needs_review),
        )

    return str(row_id)


def _insert_validation_error(
    cur: psycopg.Cursor[Any],
    *,
    run_id: str | None,
    entity_type: str,
    raw_data: dict[str, object],
    errors: list[dict[str, object]],
    source_pages: list[int] | None,
    needs_review: bool,
) -> str:
    entity_type = _non_empty_text(entity_type, "entity_type")
    _require_dict(raw_data, "raw_data")
    if not isinstance(errors, list) or any(not isinstance(error, dict) for error in errors):
        raise RepositoryError("errors must be a list of dictionaries.")

    row_id = uuid4()
    cur.execute(
        """
        INSERT INTO validation_errors (
            id, run_id, entity_type, source_pages, raw_data, errors, needs_review
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            _optional_uuid(run_id, "run_id"),
            entity_type,
            _source_pages(source_pages),
            Jsonb(raw_data),
            Jsonb(errors),
            bool(needs_review),
        ),
    )
    return str(row_id)


def _insert_magic_item(
    cur: psycopg.Cursor[Any],
    *,
    data: dict[str, object],
    row_id: UUID,
) -> None:
    name = _non_empty_text(data.get("name"), "MagicItem.name")
    attunement = data.get("attunement") if isinstance(data.get("attunement"), dict) else {}
    text = data.get("text") if isinstance(data.get("text"), dict) else {}

    cur.execute(
        """
        INSERT INTO magic_items (
            id, name, header_line, item_category, item_form, subtype, rarity, wear_slot,
            attunement_required, is_artifact, is_cursed, is_consumable, source_pages,
            raw_text, data, needs_review
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            name,
            _optional_text(data.get("header_line")),
            _optional_text(data.get("item_category")),
            _optional_text(data.get("item_form")),
            _optional_text(data.get("subtype")),
            _optional_text(data.get("rarity")),
            _optional_text(data.get("wear_slot")),
            _bool_value(attunement.get("required"), default=False),
            _bool_value(data.get("is_artifact"), default=False),
            _bool_value(data.get("is_cursed"), default=False),
            _bool_value(data.get("is_consumable"), default=False),
            _source_pages_from_data(data),
            _optional_text(text.get("raw")),
            Jsonb(data),
            _needs_review_from_data(data),
        ),
    )


def _insert_item_entity(
    cur: psycopg.Cursor[Any],
    *,
    entity_type: str,
    data: dict[str, object],
    source_pages: list[int],
    needs_review: bool,
) -> None:
    row_id = uuid4()
    cur.execute(
        """
        INSERT INTO item_entities (
            id, magic_item_id, entity_type, source_pages, data, needs_review
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (row_id, None, entity_type, source_pages, Jsonb(data), needs_review),
    )


def _path_text(path: str | Path) -> str:
    text = str(path)
    return _non_empty_text(text, "path")


def _optional_path_text(path: str | Path | None) -> str | None:
    if path is None:
        return None
    return _path_text(path)


def _optional_uuid(value: str | None, field_name: str) -> UUID | None:
    if value is None:
        return None
    return _uuid_or_error(value, field_name)


def _uuid_or_error(value: str, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise RepositoryError(f"{field_name} must be a valid UUID.") from exc


def _positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RepositoryError(f"{field_name} must be a positive integer.")
    return value


def _non_empty_text(value: object, field_name: str) -> str:
    if value is None:
        raise RepositoryError(f"{field_name} is required.")
    text = str(value).strip()
    if not text:
        raise RepositoryError(f"{field_name} must not be blank.")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_dict(value: object, field_name: str) -> None:
    if not isinstance(value, dict):
        raise RepositoryError(f"{field_name} must be a dictionary.")


def _source_pages(value: object) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RepositoryError("source_pages must be a list of positive integers.")
    pages: list[int] = []
    for page in value:
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise RepositoryError("source_pages must contain only positive integers.")
        if page not in pages:
            pages.append(page)
    return pages


def _source_pages_from_data(data: dict[str, object]) -> list[int]:
    source = data.get("source")
    if not isinstance(source, dict):
        return []
    return _source_pages(source.get("pages"))


def _needs_review_from_data(data: dict[str, object]) -> bool:
    extraction = data.get("extraction")
    if not isinstance(extraction, dict):
        return False
    return _bool_value(extraction.get("needs_review"), default=False)


def _bool_value(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _require_known_table(table_name: str) -> None:
    if table_name not in _TABLE_NAMES:
        raise RepositoryError(f"Unknown repository table: {table_name}")


__all__ = [
    "RepositoryError",
    "count_rows",
    "create_extraction_run",
    "finish_extraction_run",
    "init_db",
    "insert_assembled_item_raw",
    "insert_assembled_items_raw",
    "insert_document_page",
    "insert_document_pages",
    "insert_page_extraction_raw",
    "insert_page_extractions_raw",
    "insert_validated_entity",
    "insert_validation_error",
    "insert_validation_result",
    "list_table_counts",
    "reset_db",
]
