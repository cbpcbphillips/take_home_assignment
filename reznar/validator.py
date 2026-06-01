"""Validate mapped Reznar ontology dictionaries with the provided Pydantic models."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from reznar.ontology import REGISTRY


class OntologyValidationError(Exception):
    """Raised for unexpected ontology validation programmer errors."""


@dataclass(frozen=True, slots=True)
class ValidatedEntity:
    """One entity that validated against its ontology model."""

    entity_type: str
    data: dict[str, object]
    source_pages: list[int]
    needs_review: bool


@dataclass(frozen=True, slots=True)
class ValidationErrorRecord:
    """One entity or structure that failed ontology validation."""

    entity_type: str
    raw_data: dict[str, object]
    errors: list[dict[str, object]]
    source_pages: list[int]
    needs_review: bool


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Validation output split into valid entities and preserved errors."""

    valid_entities: list[ValidatedEntity]
    errors: list[ValidationErrorRecord]


_EXPECTED_ENTITY_GROUPS = [
    "MagicItem",
    "ItemRarityVariant",
    "ItemEffect",
    "ItemChargePool",
    "ItemUsageLimit",
    "ItemDrawback",
    "ItemLore",
]


def validate_entity(entity_type: str, raw_data: dict[str, object]) -> (
    ValidatedEntity | ValidationErrorRecord
):
    """Validate one raw entity dictionary against the registered ontology model."""

    if entity_type not in REGISTRY:
        raise OntologyValidationError(f"Unknown ontology entity type: {entity_type}")
    if not isinstance(raw_data, dict):
        return _make_structural_error(
            entity_type=entity_type,
            raw_data={"value": _json_safe(raw_data)},
            message="Entity data must be a dictionary.",
        )

    raw_copy = copy.deepcopy(raw_data)
    model_cls = REGISTRY[entity_type]
    try:
        model = model_cls.model_validate(raw_copy)
    except ValidationError as exc:
        return ValidationErrorRecord(
            entity_type=entity_type,
            raw_data=raw_copy,
            errors=_json_safe_errors(exc.errors()),
            source_pages=_extract_source_pages(raw_copy),
            needs_review=_extract_needs_review(raw_copy, default=True),
        )

    data = model.model_dump(mode="json")
    return ValidatedEntity(
        entity_type=entity_type,
        data=data,
        source_pages=_extract_source_pages(data),
        needs_review=_extract_needs_review(data, default=False),
    )


def validate_mapped_item(mapped_item: dict[str, object]) -> ValidationResult:
    """Validate one mapped item container from Module 7."""

    if not isinstance(mapped_item, dict):
        raise OntologyValidationError("mapped_item must be a dictionary.")

    valid_entities: list[ValidatedEntity] = []
    errors: list[ValidationErrorRecord] = []
    mapped_copy = copy.deepcopy(mapped_item)

    magic_item = mapped_copy.get("MagicItem")
    if "MagicItem" not in mapped_copy:
        errors.append(
            _make_structural_error(
                entity_type="MagicItem",
                raw_data={},
                message="Missing required top-level key: MagicItem.",
            )
        )
    elif not isinstance(magic_item, dict):
        errors.append(
            _make_structural_error(
                entity_type="MagicItem",
                raw_data={"value": _json_safe(magic_item)},
                message="MagicItem must be a dictionary.",
            )
        )
    else:
        _append_validation_result(validate_entity("MagicItem", magic_item), valid_entities, errors)

    for entity_type in _EXPECTED_ENTITY_GROUPS[1:]:
        group = mapped_copy.get(entity_type, [])
        if not isinstance(group, list):
            errors.append(
                _make_structural_error(
                    entity_type=entity_type,
                    raw_data={"value": _json_safe(group)},
                    message=f"{entity_type} must be a list.",
                    source_pages=_extract_source_pages(magic_item) if isinstance(magic_item, dict) else [],
                )
            )
            continue

        for index, raw_entity in enumerate(group, start=1):
            if not isinstance(raw_entity, dict):
                errors.append(
                    _make_structural_error(
                        entity_type=entity_type,
                        raw_data={"value": _json_safe(raw_entity)},
                        message=f"{entity_type}[{index}] must be a dictionary.",
                        source_pages=_extract_source_pages(magic_item)
                        if isinstance(magic_item, dict)
                        else [],
                    )
                )
                continue
            _append_validation_result(
                validate_entity(entity_type, raw_entity),
                valid_entities,
                errors,
            )

    return ValidationResult(valid_entities=valid_entities, errors=errors)


def validate_mapped_items(mapped_items: list[dict[str, object]]) -> ValidationResult:
    """Validate mapped items in order and combine all valid entities and errors."""

    valid_entities: list[ValidatedEntity] = []
    errors: list[ValidationErrorRecord] = []

    for index, mapped_item in enumerate(mapped_items, start=1):
        try:
            item_result = validate_mapped_item(mapped_item)
        except OntologyValidationError as exc:
            errors.append(
                _make_structural_error(
                    entity_type="MappedItem",
                    raw_data={"value": _json_safe(mapped_item)},
                    message=f"Mapped item {index}: {exc}",
                )
            )
            continue

        valid_entities.extend(item_result.valid_entities)
        errors.extend(
            _with_item_index_context(error_record, item_index=index)
            for error_record in item_result.errors
        )

    return ValidationResult(valid_entities=valid_entities, errors=errors)


def _append_validation_result(
    result: ValidatedEntity | ValidationErrorRecord,
    valid_entities: list[ValidatedEntity],
    errors: list[ValidationErrorRecord],
) -> None:
    if isinstance(result, ValidatedEntity):
        valid_entities.append(result)
    else:
        errors.append(result)


def _extract_source_pages(data: object) -> list[int]:
    if not isinstance(data, dict):
        return []

    source = data.get("source")
    if not isinstance(source, dict):
        return []

    pages = source.get("pages")
    if not isinstance(pages, list):
        return []

    return [
        page
        for page in pages
        if isinstance(page, int) and not isinstance(page, bool) and page >= 1
    ]


def _extract_needs_review(data: object, default: bool = False) -> bool:
    if not isinstance(data, dict):
        return default

    extraction = data.get("extraction")
    if not isinstance(extraction, dict):
        return default

    needs_review = extraction.get("needs_review")
    if isinstance(needs_review, bool):
        return needs_review
    return default


def _make_structural_error(
    *,
    entity_type: str,
    raw_data: dict[str, object],
    message: str,
    source_pages: list[int] | None = None,
    needs_review: bool = True,
) -> ValidationErrorRecord:
    return ValidationErrorRecord(
        entity_type=entity_type,
        raw_data=copy.deepcopy(raw_data),
        errors=[
            {
                "type": "structure_error",
                "loc": [],
                "msg": message,
                "input": _json_safe(raw_data),
            }
        ],
        source_pages=source_pages if source_pages is not None else _extract_source_pages(raw_data),
        needs_review=needs_review,
    )


def _with_item_index_context(
    error_record: ValidationErrorRecord,
    *,
    item_index: int,
) -> ValidationErrorRecord:
    errors = []
    for error in error_record.errors:
        contextual_error = copy.deepcopy(error)
        contextual_error["mapped_item_index"] = item_index
        errors.append(contextual_error)

    return ValidationErrorRecord(
        entity_type=error_record.entity_type,
        raw_data=copy.deepcopy(error_record.raw_data),
        errors=errors,
        source_pages=list(error_record.source_pages),
        needs_review=error_record.needs_review,
    )


def _json_safe_errors(errors: list[dict[str, Any]]) -> list[dict[str, object]]:
    return [_json_safe(error) for error in errors]


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(child) for child in value]
    return str(value)


__all__ = [
    "OntologyValidationError",
    "ValidatedEntity",
    "ValidationErrorRecord",
    "ValidationResult",
    "validate_entity",
    "validate_mapped_item",
    "validate_mapped_items",
]
