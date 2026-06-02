"""Map assembled Reznar item records into ontology-shaped dictionaries."""

from __future__ import annotations

import copy
import json
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)


class MappingError(Exception):
    """Raised when an assembled item cannot be mapped safely."""


class OntologyMapper(Protocol):
    """Interface for mapping assembled item records into ontology-shaped dictionaries."""

    def map_item(self, assembled_item: dict[str, object]) -> dict[str, object]:
        """Map one assembled item into an ontology-shaped dictionary."""


class DeterministicFallbackMapper:
    """Deterministic, no-API mapper for assembled item records."""

    def map_item(self, assembled_item: dict[str, object]) -> dict[str, object]:
        return map_assembled_item(assembled_item)


_ONTOLOGY_MAPPING_PROMPT = """
You are extracting related ontology entities for one magic item from a fantasy
catalog into ontology-shaped JSON.

Use only the provided item text and metadata. Do not invent mechanics. Prefer
fewer valid related entities over many risky entities. Use empty arrays when
unsure. Return only valid JSON. Do not include markdown, prose, or code fences.

Do not include MagicItem. Do not include unknown top-level keys. Return exactly
these top-level related-entity keys:
{
  "ItemRarityVariant": [],
  "ItemEffect": [],
  "ItemChargePool": [],
  "ItemUsageLimit": [],
  "ItemDrawback": [],
  "ItemLore": []
}

Each ItemEffect should use this simple shape:
{
  "name": "short effect name",
  "category": "defense|offense|utility|control|healing|movement|sense|transformation|summoning|storage|social|luck|environmental|other",
  "activation": "passive|action|bonus_action|reaction|command_word|on_hit|when_hit|special" or null,
  "description": "exact/mechanical summary from raw text",
  "source": {"pages": [1]},
  "extraction": {"needs_review": false, "warnings": []}
}
Avoid modifiers, damage_interactions, condition_effects, target, save, spells,
duration, and charges_cost unless extremely confident.

Each ItemUsageLimit should use this simple shape:
{
  "uses": integer or null,
  "uses_text": "three times before a short or long rest",
  "per": "day|short_rest|long_rest|short_or_long_rest|total|other" or null,
  "activation": "action|bonus_action|reaction|command_word|special" or null,
  "condition": "plain text condition",
  "notes": "plain text",
  "source": {"pages": [1]},
  "extraction": {"needs_review": false, "warnings": []}
}

Create ItemChargePool only when max_charges is explicit:
{
  "name": "Charges",
  "max_charges": 7,
  "notes": "plain text charge/recharge details",
  "source": {"pages": [1]},
  "extraction": {"needs_review": false, "warnings": []}
}

Create ItemDrawback only for a clear drawback, curse, risk, or destruction
condition. Valid kind values: curse, self_damage, ability_penalty,
vulnerability, behavioral, resource_cost, destruction_condition,
attunement_risk, other.

Use ItemLore for story, origin, history, named figures, factions, artifact lore,
and destruction text. Use ItemRarityVariant only for a clear rarity table or
variant. Valid rarity values: common, uncommon, rare, very rare, legendary,
artifact, varies, unknown.

Valid effect categories: offense, defense, utility, control, healing, movement, sense,
  transformation, summoning, storage, social, luck, environmental, other
If unsure, omit optional fields rather than guessing invalid enum values.
""".strip()


class OpenAIOntologyMapper:
    """OpenAI-backed mapper for richer ontology-shaped item records."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 90.0,
        max_retries: int = 2,
    ) -> None:
        if not api_key.strip():
            raise MappingError("OpenAI API key is required.")
        if not model.strip():
            raise MappingError("OpenAI mapper model is required.")
        if timeout <= 0:
            raise ValueError("timeout must be positive.")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative.")

        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)

    @classmethod
    def from_config(
        cls,
        config: object,
        *,
        timeout: float = 90.0,
        max_retries: int = 2,
    ) -> OpenAIOntologyMapper:
        api_key = getattr(config, "openai_api_key", None)
        model = getattr(config, "openai_mapper_model", None)
        if not isinstance(api_key, str) or not api_key.strip():
            raise MappingError("config.openai_api_key is required.")
        if not isinstance(model, str) or not model.strip():
            raise MappingError("config.openai_mapper_model is required.")
        return cls(api_key=api_key, model=model, timeout=timeout, max_retries=max_retries)

    def map_item(self, assembled_item: dict[str, object]) -> dict[str, object]:
        if not isinstance(assembled_item, dict):
            raise MappingError("Assembled item must be a dictionary.")

        deterministic = DeterministicFallbackMapper().map_item(assembled_item)
        magic_item = deterministic["MagicItem"]
        source_pages = _source_pages_from_magic_item(magic_item)
        response_text = self._call_openai(assembled_item)
        related = _parse_json_object(response_text)
        cleaned_related = _clean_related_entities(related, source_pages=source_pages)
        mapped = {"MagicItem": magic_item, **cleaned_related}
        _validate_mapped_item_structure(mapped)
        return mapped

    def _call_openai(self, assembled_item: dict[str, object]) -> str:
        payload = _assembled_item_model_payload(assembled_item)
        last_error: Exception | None = None
        attempted_retry = False

        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.responses.create(
                    model=self.model,
                    instructions=_ONTOLOGY_MAPPING_PROMPT,
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": (
                                        "Extract related entities for the following assembled item. "
                                        "Return only valid JSON. Do not include MagicItem. "
                                        "Do not include markdown, prose, or code fences."
                                    ),
                                },
                                {
                                    "type": "input_text",
                                    "text": json.dumps(payload, ensure_ascii=False),
                                }
                            ],
                        }
                    ],
                    text={"format": {"type": "json_object"}},
                    timeout=self.timeout,
                )
                return _response_output_text(response)
            except Exception as exc:
                last_error = exc
                if not _should_retry_openai_error(exc):
                    break
                if attempt < self.max_retries:
                    attempted_retry = True
                    time.sleep(_retry_delay_seconds(exc, attempt))

        if last_error is None:
            raise MappingError("OpenAI ontology mapping failed without an exception.")
        failure = (
            "OpenAI ontology mapping failed after retries"
            if attempted_retry
            else "OpenAI ontology mapping failed"
        )
        raise MappingError(f"{failure}: {_format_exception_for_message(last_error)}") from last_error


_TOP_LEVEL_KEYS = [
    "MagicItem",
    "ItemRarityVariant",
    "ItemEffect",
    "ItemChargePool",
    "ItemUsageLimit",
    "ItemDrawback",
    "ItemLore",
]
_RELATED_ENTITY_KEYS = _TOP_LEVEL_KEYS[1:]
_VALID_EFFECT_CATEGORIES = {
    "offense",
    "defense",
    "utility",
    "control",
    "healing",
    "movement",
    "sense",
    "transformation",
    "summoning",
    "storage",
    "social",
    "luck",
    "environmental",
    "other",
}
_VALID_ACTIVATIONS = {
    "passive",
    "action",
    "bonus_action",
    "reaction",
    "free_action",
    "command_word",
    "on_hit",
    "when_hit",
    "special",
}
_VALID_USAGE_PERIODS = {
    "turn",
    "round",
    "minute",
    "hour",
    "day",
    "short_rest",
    "long_rest",
    "short_or_long_rest",
    "week",
    "total",
    "other",
}
_VALID_DRAWBACK_KINDS = {
    "curse",
    "self_damage",
    "ability_penalty",
    "vulnerability",
    "behavioral",
    "resource_cost",
    "destruction_condition",
    "attunement_risk",
    "other",
}
_VALID_RARITY_VALUES = {
    "common",
    "uncommon",
    "rare",
    "very rare",
    "legendary",
    "artifact",
    "varies",
    "unknown",
}

_RARITIES = ["very rare", "common", "uncommon", "rare", "legendary", "artifact", "varies"]
_CATEGORIES = [
    "wondrous item",
    "weapon",
    "armor",
    "ring",
    "rod",
    "staff",
    "wand",
    "potion",
    "scroll",
]
_ITEM_FORMS = [
    "amulet",
    "necklace",
    "cloak",
    "cape",
    "gown",
    "boots",
    "gloves",
    "belt",
    "helm",
    "helmet",
    "mask",
    "crown",
    "headband",
    "bracers",
    "gauntlets",
    "ring",
    "armor",
    "shield",
    "dagger",
    "sword",
    "greatsword",
    "axe",
    "battleaxe",
    "mace",
    "warhammer",
    "war pick",
    "razor",
    "staff",
    "wand",
    "rod",
    "potion",
    "elixir",
    "scroll",
    "chest",
    "backpack",
    "pouch",
    "compass",
    "canteen",
    "shovel",
    "drum",
    "horn",
    "pipe",
    "instrument",
    "tool",
]


def map_assembled_item(assembled_item: dict[str, object]) -> dict[str, object]:
    """Map one assembled item dict into the expected ontology-shaped container."""

    item = copy.deepcopy(assembled_item)
    if not isinstance(item, dict):
        raise MappingError("Assembled item must be a dictionary.")

    raw_text = _required_text(item.get("raw_text"), "raw_text")
    warnings = _warnings(item.get("warnings"))
    needs_review = item.get("needs_review") is True

    name = _optional_text(item.get("name_guess"))
    if name is None:
        name = "Unknown Item"
        needs_review = True
        warnings.append("Mapper: name_guess was missing or blank; used 'Unknown Item'.")

    header_line = _optional_text(item.get("header_line"))
    source_pages = _source_pages(item.get("source_pages"))
    if source_pages is None:
        source_pages = []
        needs_review = True
        warnings.append("Mapper: source_pages was missing or invalid; used an empty list.")

    magic_item: dict[str, object] = {
        "name": name,
        "header_line": header_line,
        "tags": [],
        "attunement": _attunement(header_line, raw_text),
        "text": {"raw": raw_text},
        "source": {"pages": source_pages},
        "extraction": {
            "needs_review": needs_review,
            "warnings": warnings,
        },
        "is_artifact": False,
        "is_cursed": False,
        "is_consumable": False,
    }

    _apply_header_heuristics(magic_item, name=name, header_line=header_line)

    return {
        "MagicItem": magic_item,
        "ItemRarityVariant": [],
        "ItemEffect": [],
        "ItemChargePool": [],
        "ItemUsageLimit": [],
        "ItemDrawback": [],
        "ItemLore": [],
    }


def map_assembled_items(assembled_items: list[dict[str, object]]) -> list[dict[str, object]]:
    """Map assembled items in order."""

    mapped_items: list[dict[str, object]] = []
    for index, assembled_item in enumerate(assembled_items, start=1):
        try:
            mapped_items.append(map_assembled_item(assembled_item))
        except MappingError as exc:
            raise MappingError(f"Failed to map assembled item {index}.") from exc
    return mapped_items


def map_assembled_item_with_mapper(
    assembled_item: dict[str, object],
    mapper: OntologyMapper,
) -> dict[str, object]:
    """Map one assembled item with an explicit mapper implementation."""

    try:
        mapped = mapper.map_item(assembled_item)
    except MappingError:
        raise
    except Exception as exc:
        raise MappingError(f"Mapper failed: {exc}") from exc

    _validate_mapped_item_structure(mapped)
    return mapped


def map_assembled_items_with_mapper(
    assembled_items: list[dict[str, object]],
    mapper: OntologyMapper,
    continue_on_error: bool = True,
    fallback_mapper: OntologyMapper | None = None,
    delay_seconds: float = 0.0,
) -> list[dict[str, object]]:
    """Map assembled items with optional deterministic fallback on mapper errors."""

    if delay_seconds < 0:
        raise MappingError("delay_seconds must be non-negative.")

    mapped_items: list[dict[str, object]] = []
    errors: list[str] = []

    for index, assembled_item in enumerate(assembled_items, start=1):
        item_name = _optional_text(assembled_item.get("name_guess")) if isinstance(assembled_item, dict) else None
        context = f"item {index}"
        if item_name is not None:
            context = f"{context} ({item_name})"

        try:
            mapped_items.append(map_assembled_item_with_mapper(assembled_item, mapper))
        except Exception as exc:
            message = f"Failed to map {context}: {exc}"
            if fallback_mapper is None:
                if not continue_on_error:
                    raise MappingError(message) from exc
                errors.append(message)
                continue

            try:
                fallback = map_assembled_item_with_mapper(assembled_item, fallback_mapper)
            except Exception as fallback_exc:
                fallback_message = (
                    f"{message}; fallback mapper also failed: {fallback_exc}"
                )
                if not continue_on_error:
                    raise MappingError(fallback_message) from fallback_exc
                errors.append(fallback_message)
                continue

            _mark_ai_mapper_fallback(fallback, message)
            mapped_items.append(fallback)

        if delay_seconds > 0 and index < len(assembled_items):
            time.sleep(delay_seconds)

    if errors:
        raise MappingError("; ".join(errors))
    return mapped_items


def _assembled_item_model_payload(assembled_item: dict[str, object]) -> dict[str, object]:
    return {
        "name_guess": assembled_item.get("name_guess"),
        "header_line": assembled_item.get("header_line"),
        "source_pages": assembled_item.get("source_pages"),
        "raw_text": assembled_item.get("raw_text"),
        "warnings": assembled_item.get("warnings"),
        "fragments": assembled_item.get("fragments"),
    }


def _parse_json_object(text: str) -> dict[str, object]:
    cleaned = _strip_json_code_fence(text.strip())
    if not cleaned:
        raise MappingError("Model response was empty.")

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise MappingError(f"Model response was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise MappingError("Model response JSON must be an object.")
    return parsed


def _validate_mapped_item_structure(mapped_item: dict[str, object]) -> None:
    if not isinstance(mapped_item, dict):
        raise MappingError("Mapped item must be a dictionary.")

    actual_keys = set(mapped_item)
    expected_keys = set(_TOP_LEVEL_KEYS)
    missing_keys = expected_keys - actual_keys
    extra_keys = actual_keys - expected_keys
    if missing_keys or extra_keys:
        details: list[str] = []
        if missing_keys:
            details.append(f"missing keys: {', '.join(sorted(missing_keys))}")
        if extra_keys:
            details.append(f"extra keys: {', '.join(sorted(extra_keys))}")
        raise MappingError(f"Mapped item top-level keys are invalid ({'; '.join(details)}).")

    if not isinstance(mapped_item["MagicItem"], dict):
        raise MappingError("Mapped item MagicItem must be a dictionary.")

    for key in _TOP_LEVEL_KEYS[1:]:
        if not isinstance(mapped_item[key], list):
            raise MappingError(f"Mapped item {key} must be a list.")


def _clean_related_entities(
    related: dict[str, object],
    *,
    source_pages: list[int],
) -> dict[str, list[dict[str, object]]]:
    cleaned: dict[str, list[dict[str, object]]] = {key: [] for key in _RELATED_ENTITY_KEYS}
    for key in _RELATED_ENTITY_KEYS:
        records = related.get(key, [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            cleaned_record = _clean_related_entity(key, record, source_pages=source_pages)
            if cleaned_record is not None:
                cleaned[key].append(cleaned_record)
    return cleaned


def _clean_related_entity(
    entity_type: str,
    record: dict[str, object],
    *,
    source_pages: list[int],
) -> dict[str, object] | None:
    if entity_type == "ItemEffect":
        return _clean_item_effect(record, source_pages=source_pages)
    if entity_type == "ItemUsageLimit":
        return _clean_item_usage_limit(record, source_pages=source_pages)
    if entity_type == "ItemChargePool":
        return _clean_item_charge_pool(record, source_pages=source_pages)
    if entity_type == "ItemDrawback":
        return _clean_item_drawback(record, source_pages=source_pages)
    if entity_type == "ItemLore":
        return _clean_item_lore(record, source_pages=source_pages)
    if entity_type == "ItemRarityVariant":
        return _clean_item_rarity_variant(record)
    return None


def _clean_item_effect(
    record: dict[str, object],
    *,
    source_pages: list[int],
) -> dict[str, object] | None:
    description = _optional_text(record.get("description"))
    if description is None:
        return None

    category = _normalize_enum(record.get("category"), _VALID_EFFECT_CATEGORIES)
    activation = _normalize_enum(record.get("activation"), _VALID_ACTIVATIONS)
    cleaned: dict[str, object] = {
        "category": category or "other",
        "description": description,
        "source": _clean_source(record.get("source"), source_pages),
        "extraction": _clean_extraction(record.get("extraction")),
    }
    name = _optional_text(record.get("name"))
    if name is not None:
        cleaned["name"] = name
    if activation is not None:
        cleaned["activation"] = activation
    return cleaned


def _clean_item_usage_limit(
    record: dict[str, object],
    *,
    source_pages: list[int],
) -> dict[str, object] | None:
    cleaned: dict[str, object] = {
        "source": _clean_source(record.get("source"), source_pages),
        "extraction": _clean_extraction(record.get("extraction")),
    }
    uses = _positive_int_or_none(record.get("uses"))
    if uses is not None:
        cleaned["uses"] = uses
    for key in ("uses_text", "condition", "notes"):
        value = _optional_text(record.get(key))
        if value is not None:
            cleaned[key] = value
    per = _normalize_enum(record.get("per"), _VALID_USAGE_PERIODS)
    if per is not None:
        cleaned["per"] = per
    activation = _normalize_enum(record.get("activation"), _VALID_ACTIVATIONS)
    if activation is not None:
        cleaned["activation"] = activation

    return cleaned if any(key in cleaned for key in ("uses", "uses_text", "condition", "notes")) else None


def _clean_item_charge_pool(
    record: dict[str, object],
    *,
    source_pages: list[int],
) -> dict[str, object] | None:
    max_charges = _positive_int_or_none(record.get("max_charges"))
    if max_charges is None:
        return None

    cleaned: dict[str, object] = {
        "max_charges": max_charges,
        "source": _clean_source(record.get("source"), source_pages),
        "extraction": _clean_extraction(record.get("extraction")),
    }
    name = _optional_text(record.get("name"))
    if name is not None:
        cleaned["name"] = name
    notes = _optional_text(record.get("notes"))
    if notes is not None:
        cleaned["notes"] = notes
    return cleaned


def _clean_item_drawback(
    record: dict[str, object],
    *,
    source_pages: list[int],
) -> dict[str, object] | None:
    trigger = _optional_text(record.get("trigger"))
    penalty = _optional_text(record.get("penalty"))
    removal_condition = _optional_text(record.get("removal_condition"))
    notes = _optional_text(record.get("notes"))
    if trigger is None and penalty is None and removal_condition is None and notes is None:
        return None

    kind = _normalize_enum(record.get("kind"), _VALID_DRAWBACK_KINDS) or "other"
    cleaned: dict[str, object] = {
        "kind": kind,
        "source": _clean_source(record.get("source"), source_pages),
        "extraction": _clean_extraction(record.get("extraction")),
    }
    if trigger is not None:
        cleaned["trigger"] = trigger
    if penalty is not None:
        cleaned["penalty"] = penalty
    if removal_condition is not None:
        cleaned["removal_condition"] = removal_condition
    if notes is not None:
        cleaned["notes"] = notes
    return cleaned


def _clean_item_lore(
    record: dict[str, object],
    *,
    source_pages: list[int],
) -> dict[str, object] | None:
    cleaned: dict[str, object] = {
        "source": _clean_source(record.get("source"), source_pages),
        "extraction": _clean_extraction(record.get("extraction")),
    }
    for key in ("summary", "full_text", "origin", "destruction_condition", "notes"):
        value = _optional_text(record.get(key))
        if value is not None:
            cleaned[key] = value
    for key in ("named_figures", "factions"):
        values = _text_list(record.get(key))
        if values:
            cleaned[key] = values
    lore_keys = {
        "summary",
        "full_text",
        "origin",
        "destruction_condition",
        "notes",
        "named_figures",
        "factions",
    }
    return cleaned if any(key in cleaned for key in lore_keys) else None


def _clean_item_rarity_variant(record: dict[str, object]) -> dict[str, object] | None:
    condition = _optional_text(record.get("condition"))
    rarity = _normalize_enum(record.get("rarity"), _VALID_RARITY_VALUES)
    if condition is None or rarity is None:
        return None

    cleaned: dict[str, object] = {"condition": condition, "rarity": rarity}
    notes = _optional_text(record.get("notes"))
    if notes is not None:
        cleaned["notes"] = notes
    return cleaned


def _clean_source(value: object, fallback_pages: list[int]) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    pages = _normalize_source_pages(source.get("pages"), fallback_pages=fallback_pages)
    return {"pages": pages}


def _clean_extraction(value: object) -> dict[str, object]:
    extraction = value if isinstance(value, dict) else {}
    needs_review = extraction.get("needs_review")
    warnings = extraction.get("warnings")
    return {
        "needs_review": needs_review if isinstance(needs_review, bool) else False,
        "warnings": _text_list(warnings),
    }


def _source_pages_from_magic_item(magic_item: object) -> list[int]:
    if not isinstance(magic_item, dict):
        return []
    source = magic_item.get("source")
    if not isinstance(source, dict):
        return []
    return _normalize_source_pages(source.get("pages"), fallback_pages=[])


def _normalize_source_pages(value: object, *, fallback_pages: list[int]) -> list[int]:
    if not isinstance(value, list):
        return list(fallback_pages)
    pages: list[int] = []
    for page in value:
        parsed = _positive_int_or_none(page)
        if parsed is not None and parsed not in pages:
            pages.append(parsed)
    return pages or list(fallback_pages)


def _normalize_enum(value: object, valid_values: set[str]) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    normalized = _normalize(text)
    aliases = {
        "bonus action": "bonus_action",
        "free action": "free_action",
        "command word": "command_word",
        "on hit": "on_hit",
        "when hit": "when_hit",
        "short rest": "short_rest",
        "long rest": "long_rest",
        "short or long rest": "short_or_long_rest",
        "short long rest": "short_or_long_rest",
        "veryrare": "very rare",
        "very rare": "very rare",
        "self damage": "self_damage",
        "ability penalty": "ability_penalty",
        "resource cost": "resource_cost",
        "destruction condition": "destruction_condition",
        "attunement risk": "attunement_risk",
    }
    normalized = aliases.get(normalized, normalized.replace(" ", "_"))
    if normalized in valid_values:
        return normalized
    text_value = text.strip().lower()
    return text_value if text_value in valid_values else None


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float) and value.is_integer():
        parsed = int(value)
        return parsed if parsed >= 1 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed >= 1 else None
    return None


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text is not None:
            values.append(text)
    return values


def _mark_ai_mapper_fallback(mapped_item: dict[str, object], failure_message: str) -> None:
    magic_item = mapped_item.get("MagicItem")
    if not isinstance(magic_item, dict):
        return

    extraction = magic_item.get("extraction")
    if not isinstance(extraction, dict):
        extraction = {}
        magic_item["extraction"] = extraction

    extraction["needs_review"] = True
    warnings = extraction.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warning = "AI related entity mapping failed; deterministic MagicItem only."
    if warning not in warnings:
        warnings.append(warning)
    details = f"AI mapper failure: {failure_message}"
    if details not in warnings:
        warnings.append(details)
    extraction["warnings"] = warnings


def _strip_json_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return text

    first_line = lines[0].strip().lower()
    if first_line not in {"```", "```json"}:
        return text
    return "\n".join(lines[1:-1]).strip()


def _response_output_text(response: object) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = getattr(response, "output", None)
    if isinstance(output, list):
        text_parts: list[str] = []
        for item in output:
            content = getattr(item, "content", None)
            if not isinstance(content, list):
                continue
            for content_item in content:
                text = getattr(content_item, "text", None)
                if isinstance(text, str):
                    text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts)

    raise MappingError("OpenAI response did not include text output.")


def _should_retry_openai_error(exc: Exception) -> bool:
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return False
    if _is_insufficient_quota_error(exc):
        return False
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        status_code = getattr(exc, "status_code", None)
        return status_code in {408, 409, 429} or (
            isinstance(status_code, int) and status_code >= 500
        )
    return isinstance(exc, APIError)


def _is_insufficient_quota_error(exc: Exception) -> bool:
    for value in _error_values(exc):
        if str(value).strip().lower() == "insufficient_quota":
            return True
    return "insufficient_quota" in str(exc).lower()


def _error_values(exc: Exception) -> list[object]:
    values: list[object] = []
    for attribute in ("code", "type"):
        value = getattr(exc, attribute, None)
        if value is not None:
            values.append(value)

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        values.extend(_dict_values(body))
    return values


def _dict_values(data: dict[object, object]) -> list[object]:
    values: list[object] = []
    for value in data.values():
        values.append(value)
        if isinstance(value, dict):
            values.extend(_dict_values(value))
    return values


def _retry_delay_seconds(exc: Exception, attempt: int) -> float:
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return retry_after
    return min(30.0, 2.0**attempt)


def _retry_after_seconds(exc: Exception) -> float | None:
    value = _retry_after_header(exc)
    if not value:
        return None

    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at - datetime.now(UTC)).total_seconds()

    return max(0.0, seconds)


def _retry_after_header(exc: Exception) -> str | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value is not None:
            return str(value)

    headers = getattr(exc, "headers", None)
    if headers is not None:
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value is not None:
            return str(value)
    return None


def _format_exception_for_message(exc: Exception) -> str:
    message = _sanitize_error_message(str(exc).strip())
    if not message:
        message = "(no message)"
    return f"{type(exc).__name__}: {message}"


def _sanitize_error_message(message: str) -> str:
    redacted = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-[redacted]", message)
    return re.sub(r"Bearer\s+\S+", "Bearer [redacted]", redacted, flags=re.IGNORECASE)


def _apply_header_heuristics(
    magic_item: dict[str, object],
    *,
    name: str,
    header_line: str | None,
) -> None:
    header = header_line or ""
    normalized_header = _normalize(header)

    category = _first_present(normalized_header, _CATEGORIES)
    if category is not None:
        magic_item["item_category"] = category

    rarity = _first_present(normalized_header, _RARITIES)
    if rarity is not None:
        magic_item["rarity"] = rarity

    subtype = _subtype_from_header(header, category)
    if subtype is not None:
        magic_item["subtype"] = subtype

    item_form = _item_form(name=name, header_line=header, subtype=subtype, category=category)
    if item_form is not None:
        magic_item["item_form"] = item_form


def _attunement(header_line: str | None, raw_text: str) -> dict[str, object]:
    combined_text = " ".join(part for part in [header_line, raw_text] if part)
    raw_attunement = _attunement_text(combined_text)
    return {
        "required": "requires attunement" in _normalize(combined_text),
        "raw_text": raw_attunement,
    }


def _attunement_text(text: str) -> str | None:
    match = re.search(r"requires\s+attunement(?:[^.;\n)]*)", text, flags=re.IGNORECASE)
    if match is None:
        return None
    return " ".join(match.group(0).split())


def _subtype_from_header(header_line: str, category: str | None) -> str | None:
    if category is None:
        return None

    pattern = rf"{re.escape(category)}\s*\(([^)]+)\)"
    match = re.search(pattern, header_line, flags=re.IGNORECASE)
    if match is None:
        return None
    subtype = " ".join(match.group(1).split()).lower()
    return subtype or None


def _item_form(
    *,
    name: str,
    header_line: str,
    subtype: str | None,
    category: str | None,
) -> str | None:
    for candidate in [subtype, category]:
        if candidate in _ITEM_FORMS:
            return candidate

    searchable = _normalize(f"{name} {header_line}")
    for item_form in _ITEM_FORMS:
        if _contains_word(searchable, item_form):
            return item_form
    return None


def _first_present(text: str, candidates: list[str]) -> str | None:
    for candidate in candidates:
        if _contains_word(text, candidate):
            return candidate
    return None


def _contains_word(text: str, phrase: str) -> bool:
    normalized_text = _normalize(text)
    normalized_phrase = _normalize(phrase)
    return re.search(rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)", normalized_text) is not None


def _required_text(value: object, field_name: str) -> str:
    if value is None:
        raise MappingError(f"Assembled item missing required {field_name}.")
    text = str(value)
    if not text.strip():
        raise MappingError(f"Assembled item {field_name} is blank.")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _source_pages(value: object) -> list[int] | None:
    if not isinstance(value, list):
        return None
    if any(not isinstance(page, int) or isinstance(page, bool) or page < 1 for page in value):
        return None

    pages: list[int] = []
    for page in value:
        if page not in pages:
            pages.append(page)
    return pages


def _warnings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    warnings: list[str] = []
    for warning in value:
        text = str(warning).strip()
        if text:
            warnings.append(text)
    return warnings


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("_", " ").replace("-", " ").split())


__all__ = [
    "DeterministicFallbackMapper",
    "MappingError",
    "OpenAIOntologyMapper",
    "OntologyMapper",
    "map_assembled_item",
    "map_assembled_item_with_mapper",
    "map_assembled_items",
    "map_assembled_items_with_mapper",
]
