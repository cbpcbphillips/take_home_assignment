"""Load validated Reznar ontology entities into canonical database tables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from db import connect


class LoaderError(Exception):
    """Raised when canonical entity loading fails."""


@dataclass(frozen=True, slots=True)
class LoadResult:
    """Result of loading one validated item into canonical tables."""

    magic_item_id: str | None
    inserted_magic_item: bool
    updated_magic_item: bool
    item_entity_ids: list[str]
    skipped_entities: list[str]
    warnings: list[str]


def load_magic_item_entity(entity: object) -> tuple[str, bool, bool]:
    """Insert or update one validated MagicItem entity."""

    _require_entity_type(entity, expected="MagicItem")
    _validate_magic_item_entity(entity)
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                return _upsert_magic_item(cur, entity)
    except LoaderError:
        raise
    except Exception as exc:
        raise LoaderError("Failed to load MagicItem entity.") from exc


def load_item_entity(magic_item_id: str, entity: object) -> str:
    """Insert one validated non-MagicItem entity linked to a magic item."""

    magic_item_uuid = _uuid_or_error(magic_item_id, "magic_item_id")
    entity_type = _entity_type(entity)
    if entity_type == "MagicItem":
        raise LoaderError("load_item_entity requires a non-MagicItem entity.")
    _validate_item_entity(entity)

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                return _insert_item_entity(cur, magic_item_uuid=magic_item_uuid, entity=entity)
    except LoaderError:
        raise
    except Exception as exc:
        raise LoaderError(f"Failed to load item entity {entity_type}.") from exc


def load_validation_result(validation_result: object) -> LoadResult:
    """Load one ValidationResult-like object into canonical tables."""

    valid_entities = _valid_entities(validation_result)
    magic_entities = [entity for entity in valid_entities if _entity_type(entity) == "MagicItem"]
    related_entities = [entity for entity in valid_entities if _entity_type(entity) != "MagicItem"]

    if not magic_entities:
        return LoadResult(
            magic_item_id=None,
            inserted_magic_item=False,
            updated_magic_item=False,
            item_entity_ids=[],
            skipped_entities=[_entity_type(entity) for entity in related_entities],
            warnings=["Validation result did not contain a valid MagicItem; skipped related entities."],
        )

    if len(magic_entities) > 1:
        raise LoaderError("Validation result contains more than one MagicItem entity.")

    _validate_magic_item_entity(magic_entities[0])
    for entity in related_entities:
        _validate_item_entity(entity)

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                magic_item_id, inserted, updated = _upsert_magic_item(cur, magic_entities[0])
                magic_item_uuid = _uuid_or_error(magic_item_id, "magic_item_id")
                _delete_item_entities_for_magic_item(cur, magic_item_uuid)

                item_entity_ids = [
                    _insert_item_entity(cur, magic_item_uuid=magic_item_uuid, entity=entity)
                    for entity in related_entities
                ]
    except LoaderError:
        raise
    except Exception as exc:
        raise LoaderError("Failed to load validation result.") from exc

    return LoadResult(
        magic_item_id=magic_item_id,
        inserted_magic_item=inserted,
        updated_magic_item=updated,
        item_entity_ids=item_entity_ids,
        skipped_entities=[],
        warnings=[],
    )


def load_validation_results(validation_results: list[object]) -> list[LoadResult]:
    """Load validation results in order."""

    load_results: list[LoadResult] = []
    for index, validation_result in enumerate(validation_results, start=1):
        try:
            load_results.append(load_validation_result(validation_result))
        except LoaderError as exc:
            raise LoaderError(f"Failed to load validation result {index}.") from exc
    return load_results


def _upsert_magic_item(cur: psycopg.Cursor[Any], entity: object) -> tuple[str, bool, bool]:
    data = _entity_data(entity)
    name = _non_empty_text(data.get("name"), "MagicItem.name")
    source_pages = _source_pages_from_data(data, fallback=_entity_source_pages(entity))
    needs_review = _needs_review_from_data(data, fallback=_entity_needs_review(entity))
    existing_id = _find_existing_magic_item(cur, name=name, source_pages=source_pages)

    if existing_id is None:
        magic_item_id = uuid4()
        _insert_magic_item(
            cur,
            magic_item_id=magic_item_id,
            data=data,
            name=name,
            source_pages=source_pages,
            needs_review=needs_review,
        )
        return str(magic_item_id), True, False

    _update_magic_item(
        cur,
        magic_item_id=existing_id,
        data=data,
        name=name,
        source_pages=source_pages,
        needs_review=needs_review,
    )
    return str(existing_id), False, True


def _find_existing_magic_item(
    cur: psycopg.Cursor[Any],
    *,
    name: str,
    source_pages: list[int],
) -> UUID | None:
    cur.execute(
        """
        SELECT id
        FROM magic_items
        WHERE name = %s
          AND source_pages = %s::integer[]
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (name, source_pages),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return row[0]


def _insert_magic_item(
    cur: psycopg.Cursor[Any],
    *,
    magic_item_id: UUID,
    data: dict[str, object],
    name: str,
    source_pages: list[int],
    needs_review: bool,
) -> None:
    cur.execute(
        """
        INSERT INTO magic_items (
            id, name, header_line, item_category, item_form, subtype, rarity, wear_slot,
            attunement_required, is_artifact, is_cursed, is_consumable, source_pages,
            raw_text, data, needs_review
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::integer[], %s, %s, %s)
        """,
        (
            magic_item_id,
            name,
            _optional_text(data.get("header_line")),
            _optional_text(data.get("item_category")),
            _optional_text(data.get("item_form")),
            _optional_text(data.get("subtype")),
            _optional_text(data.get("rarity")),
            _optional_text(data.get("wear_slot")),
            _attunement_required(data),
            _bool_value(data.get("is_artifact"), default=False),
            _bool_value(data.get("is_cursed"), default=False),
            _bool_value(data.get("is_consumable"), default=False),
            source_pages,
            _raw_text(data),
            Jsonb(data),
            needs_review,
        ),
    )


def _update_magic_item(
    cur: psycopg.Cursor[Any],
    *,
    magic_item_id: UUID,
    data: dict[str, object],
    name: str,
    source_pages: list[int],
    needs_review: bool,
) -> None:
    cur.execute(
        """
        UPDATE magic_items
        SET header_line = %s,
            item_category = %s,
            item_form = %s,
            subtype = %s,
            rarity = %s,
            wear_slot = %s,
            attunement_required = %s,
            is_artifact = %s,
            is_cursed = %s,
            is_consumable = %s,
            raw_text = %s,
            data = %s,
            needs_review = %s,
            updated_at = now()
        WHERE id = %s
          AND name = %s
          AND source_pages = %s::integer[]
        """,
        (
            _optional_text(data.get("header_line")),
            _optional_text(data.get("item_category")),
            _optional_text(data.get("item_form")),
            _optional_text(data.get("subtype")),
            _optional_text(data.get("rarity")),
            _optional_text(data.get("wear_slot")),
            _attunement_required(data),
            _bool_value(data.get("is_artifact"), default=False),
            _bool_value(data.get("is_cursed"), default=False),
            _bool_value(data.get("is_consumable"), default=False),
            _raw_text(data),
            Jsonb(data),
            needs_review,
            magic_item_id,
            name,
            source_pages,
        ),
    )
    if cur.rowcount != 1:
        raise LoaderError(f"MagicItem row disappeared during update: {magic_item_id}")


def _insert_item_entity(
    cur: psycopg.Cursor[Any],
    *,
    magic_item_uuid: UUID,
    entity: object,
) -> str:
    entity_type = _entity_type(entity)
    if entity_type == "MagicItem":
        raise LoaderError("Cannot insert MagicItem into item_entities.")

    data = _entity_data(entity)
    row_id = uuid4()
    cur.execute(
        """
        INSERT INTO item_entities (
            id, magic_item_id, entity_type, source_pages, data, needs_review
        )
        VALUES (%s, %s, %s, %s::integer[], %s, %s)
        """,
        (
            row_id,
            magic_item_uuid,
            entity_type,
            _source_pages_from_data(data, fallback=_entity_source_pages(entity)),
            Jsonb(data),
            _needs_review_from_data(data, fallback=_entity_needs_review(entity)),
        ),
    )
    return str(row_id)


def _delete_item_entities_for_magic_item(cur: psycopg.Cursor[Any], magic_item_id: UUID) -> None:
    cur.execute("DELETE FROM item_entities WHERE magic_item_id = %s", (magic_item_id,))


def _valid_entities(validation_result: object) -> list[object]:
    valid_entities = getattr(validation_result, "valid_entities", None)
    if not isinstance(valid_entities, list):
        raise LoaderError("validation_result.valid_entities must be a list.")
    return valid_entities


def _validate_magic_item_entity(entity: object) -> None:
    data = _entity_data(entity)
    _non_empty_text(data.get("name"), "MagicItem.name")
    _source_pages_from_data(data, fallback=_entity_source_pages(entity))
    _needs_review_from_data(data, fallback=_entity_needs_review(entity))


def _validate_item_entity(entity: object) -> None:
    data = _entity_data(entity)
    _source_pages_from_data(data, fallback=_entity_source_pages(entity))
    _needs_review_from_data(data, fallback=_entity_needs_review(entity))


def _require_entity_type(entity: object, *, expected: str) -> None:
    actual = _entity_type(entity)
    if actual != expected:
        raise LoaderError(f"Expected entity_type {expected}, got {actual}.")


def _entity_type(entity: object) -> str:
    entity_type = getattr(entity, "entity_type", None)
    return _non_empty_text(entity_type, "entity.entity_type")


def _entity_data(entity: object) -> dict[str, object]:
    data = getattr(entity, "data", None)
    if not isinstance(data, dict):
        raise LoaderError("entity.data must be a dictionary.")
    return data


def _entity_source_pages(entity: object) -> list[int]:
    return _source_pages(getattr(entity, "source_pages", None))


def _entity_needs_review(entity: object) -> bool:
    return _bool_value(getattr(entity, "needs_review", None), default=False)


def _source_pages_from_data(data: dict[str, object], fallback: list[int]) -> list[int]:
    source = data.get("source")
    if not isinstance(source, dict):
        return list(fallback)

    pages = source.get("pages")
    if pages is None:
        return list(fallback)
    return _source_pages(pages)


def _needs_review_from_data(data: dict[str, object], fallback: bool) -> bool:
    extraction = data.get("extraction")
    if not isinstance(extraction, dict):
        return fallback
    return _bool_value(extraction.get("needs_review"), default=fallback)


def _source_pages(value: object) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise LoaderError("source_pages must be a list of positive integers.")

    pages: list[int] = []
    for page in value:
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise LoaderError("source_pages must contain only positive integers.")
        if page not in pages:
            pages.append(page)
    return pages


def _attunement_required(data: dict[str, object]) -> bool:
    attunement = data.get("attunement")
    if not isinstance(attunement, dict):
        return False
    return _bool_value(attunement.get("required"), default=False)


def _raw_text(data: dict[str, object]) -> str | None:
    text = data.get("text")
    if not isinstance(text, dict):
        return None
    return _optional_text(text.get("raw"))


def _uuid_or_error(value: str, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise LoaderError(f"{field_name} must be a valid UUID.") from exc


def _non_empty_text(value: object, field_name: str) -> str:
    if value is None:
        raise LoaderError(f"{field_name} is required.")
    text = str(value).strip()
    if not text:
        raise LoaderError(f"{field_name} must not be blank.")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_value(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


__all__ = [
    "LoadResult",
    "LoaderError",
    "load_item_entity",
    "load_magic_item_entity",
    "load_validation_result",
    "load_validation_results",
]
