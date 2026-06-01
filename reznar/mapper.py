"""Map assembled Reznar item records into ontology-shaped dictionaries."""

from __future__ import annotations

import copy
import re
from typing import Protocol


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


_TOP_LEVEL_KEYS = [
    "MagicItem",
    "ItemRarityVariant",
    "ItemEffect",
    "ItemChargePool",
    "ItemUsageLimit",
    "ItemDrawback",
    "ItemLore",
]

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
    "OntologyMapper",
    "map_assembled_item",
    "map_assembled_items",
]
