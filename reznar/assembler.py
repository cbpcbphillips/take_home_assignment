"""Assemble raw page-level extractions into item-level records."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import orjson


class AssemblyError(Exception):
    """Raised when raw page extractions cannot be assembled safely."""


_JSON_OPTIONS = orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS
_RECOGNIZED_HEADER_STARTS = (
    "wondrous item",
    "wonderous item",
    "weapon",
    "armor",
    "ring",
    "potion",
    "scroll",
    "wand",
    "staff",
    "rod",
)
_CONTINUATION_STARTS = (
    "after much deliberation",
    "unfortunately",
    "while the orcs",
    "the powers of",
    "eventually",
    "the ring, however",
    "this suit",
    "in addition",
)
_SUSPICIOUS_HEADER_STARTS = (
    "immunities",
    "resistances",
    "increased movement",
    "increased appetite",
    "destroying the drum",
    "destroying the armor",
    "destroying the ring",
    "heightened senses",
    "regeneration",
    "increased ability scores",
    "spellcasting",
    "summon warriors",
    "vulnerability",
    "energy leech",
)
_KNOWN_REPAIR_RANGES = {
    "exo armor": (7, 9),
    "ring of elven lords": (22, 24),
    "war drum of the horde": (29, 31),
    "pouch of false coins": (34, 35),
}


def assembled_item_path(output_dir: Path, item_index: int) -> Path:
    """Return the deterministic assembled JSON path for a 1-based item index."""

    if not isinstance(item_index, int) or isinstance(item_index, bool) or item_index < 1:
        raise AssemblyError(f"Invalid item index {item_index}; expected a 1-based index.")
    return Path(output_dir) / f"item_{item_index:03d}.json"


def assemble_items(page_extractions: list[dict[str, object]]) -> list[dict[str, object]]:
    """Assemble sorted page extraction dictionaries into item-level records."""

    sorted_pages = sorted(
        (_validate_page_extraction(page) for page in page_extractions),
        key=lambda page: page["page_number"],
    )

    assembled: list[dict[str, object]] = []
    open_item: dict[str, object] | None = None

    for page in sorted_pages:
        page_number = page["page_number"]
        page_warnings = _normalize_page_warnings(page["warnings"], page_number)

        if not page["items"]:
            if open_item is not None:
                _add_warnings(open_item, page_warnings)
            continue

        for raw_item in page["items"]:
            fragment = _item_to_fragment(raw_item, page_number)
            continues_from_previous = bool(fragment["continues_from_previous_page"])
            continues_to_next = bool(fragment["continues_to_next_page"])

            if continues_from_previous:
                if open_item is not None and _last_fragment_page(open_item) == page_number - 1:
                    _append_fragment(open_item, fragment, page_warnings)
                else:
                    if open_item is not None:
                        _mark_needs_review(
                            open_item,
                            (
                                f"Page {page_number}: previous item expected continuation, "
                                "but no continuation from the immediately previous page was found."
                            ),
                        )
                        _finalize_item(open_item, assembled)
                    open_item = _new_assembled_item(fragment, page_warnings)
                    _mark_needs_review(
                        open_item,
                        (
                            f"Page {page_number}: item marked continues_from_previous_page "
                            "but no previous open item was available."
                        ),
                    )
            else:
                if open_item is not None:
                    _mark_needs_review(
                        open_item,
                        (
                            f"Page {page_number}: previous item expected continuation, "
                            "but the next item did not mark continues_from_previous_page."
                        ),
                    )
                    _finalize_item(open_item, assembled)
                    open_item = None

                open_item = _new_assembled_item(fragment, page_warnings)

            if not continues_to_next and open_item is not None:
                _finalize_item(open_item, assembled)
                open_item = None

    if open_item is not None:
        _mark_needs_review(
            open_item,
            "Final item claimed to continue, but no later page was available.",
        )
        _finalize_item(open_item, assembled)

    return assembled


def repair_assembled_items(assembled_items: list[dict[str, object]]) -> list[dict[str, object]]:
    """Merge obvious multi-page continuation records without mutating input records."""

    sorted_items = [
        item
        for _, item in sorted(
            ((index, copy.deepcopy(item)) for index, item in enumerate(assembled_items)),
            key=lambda indexed_item: (_first_source_page(indexed_item[1]), indexed_item[0]),
        )
    ]

    repaired: list[dict[str, object]] = []
    for candidate in sorted_items:
        if not isinstance(candidate, dict):
            repaired.append(candidate)
            continue

        parent = repaired[-1] if repaired else None
        if parent is not None and _should_merge_continuation(parent, candidate):
            repaired[-1] = _merge_assembled_item(parent, candidate)
        else:
            repaired.append(candidate)

    return repaired


def find_suspicious_assembled_items(
    assembled_items: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Return summary dictionaries for assembled items that need human review."""

    name_counts: dict[str, int] = {}
    for item in assembled_items:
        if not isinstance(item, dict):
            continue
        normalized = _normalized_name(item.get("name_guess"))
        if normalized:
            name_counts[normalized] = name_counts.get(normalized, 0) + 1

    suspicious: list[dict[str, object]] = []
    for index, item in enumerate(assembled_items, start=1):
        if not isinstance(item, dict):
            suspicious.append(
                {
                    "index": index,
                    "name_guess": None,
                    "header_line": None,
                    "source_pages": [],
                    "reasons": ["record is not an object"],
                }
            )
            continue

        reasons: list[str] = []
        name_guess = _optional_text(item.get("name_guess"))
        header_line = _optional_text(item.get("header_line"))
        raw_text = _optional_text(item.get("raw_text"))
        source_pages = _source_pages_from_item(item)
        normalized = _normalized_name(name_guess)

        if normalized and name_counts.get(normalized, 0) > 1:
            reasons.append("duplicate name_guess")
        if not header_line or not _has_recognized_item_header(header_line):
            reasons.append("unrecognized header_line")
        if len(source_pages) > 1:
            reasons.append("multi-page source_pages")
        if item.get("needs_review") is True:
            reasons.append("needs_review=True")
        if any(_mentions_repair_or_continuation(warning) for warning in _warnings_from_item(item)):
            reasons.append("repair/continuation warning")
        if not name_guess:
            reasons.append("blank or missing name_guess")
        if not raw_text:
            reasons.append("blank or missing raw_text")

        if reasons:
            suspicious.append(
                {
                    "index": index,
                    "name_guess": name_guess,
                    "header_line": header_line,
                    "source_pages": source_pages,
                    "reasons": reasons,
                }
            )

    return suspicious


def save_assembled_items(
    assembled_items: list[dict[str, object]],
    output_dir: Path,
    force: bool = True,
) -> list[Path]:
    """Save assembled items as stable, human-readable UTF-8 JSON files."""

    target_dir = Path(output_dir)
    if target_dir.is_symlink():
        raise AssemblyError(f"Refusing to write into symlinked output_dir: {target_dir}")

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise AssemblyError(f"Failed to create assembled output directory: {target_dir}") from exc

    saved_paths: list[Path] = []
    for index, item in enumerate(assembled_items, start=1):
        path = assembled_item_path(target_dir, index)
        _validate_output_path(path, target_dir)
        if path.exists() and not force:
            raise AssemblyError(f"Assembled item already exists and force=False: {path}")

        try:
            payload = orjson.dumps(copy.deepcopy(item), option=_JSON_OPTIONS, default=_json_default)
            path.write_bytes(payload + b"\n")
        except Exception as exc:
            raise AssemblyError(f"Failed to save assembled item JSON at {path}.") from exc
        saved_paths.append(path)

    if force:
        _delete_stale_assembled_items(target_dir, len(saved_paths))

    return saved_paths


def load_assembled_item(path: Path) -> dict[str, object]:
    """Load one assembled item JSON object."""

    source_path = Path(path)
    try:
        parsed = orjson.loads(source_path.read_bytes())
    except Exception as exc:
        raise AssemblyError(f"Failed to load assembled item JSON at {source_path}.") from exc

    if not isinstance(parsed, dict):
        raise AssemblyError(f"Assembled item JSON must contain an object: {source_path}")
    return parsed


def assemble_items_from_config(
    config: object,
    page_extractions: list[dict[str, object]],
    save: bool = False,
    force: bool = True,
) -> list[dict[str, object]]:
    """Assemble items using a PipelineConfig-like object."""

    assembled = assemble_items(page_extractions)
    if save:
        save_assembled_items(assembled, config.assembled_dir, force=force)
    return assembled


def _should_merge_continuation(
    parent: dict[str, object],
    candidate: dict[str, object],
) -> bool:
    parent_pages = _source_pages_from_item(parent)
    candidate_pages = _source_pages_from_item(candidate)
    if not parent_pages or not candidate_pages:
        return False

    parent_last_page = max(parent_pages)
    candidate_first_page = min(candidate_pages)
    if candidate_first_page <= parent_last_page:
        return False

    page_gap = candidate_first_page - parent_last_page
    if page_gap > 2:
        return False

    if _is_clear_distinct_item(parent, candidate):
        return False

    parent_name = _normalized_name(parent.get("name_guess"))
    candidate_name = _normalized_name(candidate.get("name_guess"))
    same_name = bool(parent_name and candidate_name and parent_name == candidate_name)
    known_range = _known_repair_range_matches(parent, candidate_first_page)
    suspicious_candidate = _is_suspicious_continuation_candidate(candidate)
    continuation_start = _has_continuation_start(candidate)
    likely_long_parent = _is_likely_long_form_item(parent)

    if same_name and (known_range or likely_long_parent or suspicious_candidate):
        return True
    if known_range and (same_name or suspicious_candidate or continuation_start):
        return True
    if page_gap == 1 and likely_long_parent and (suspicious_candidate or continuation_start):
        return True
    return False


def _is_clear_distinct_item(parent: dict[str, object], candidate: dict[str, object]) -> bool:
    parent_name = _normalized_name(parent.get("name_guess"))
    candidate_name = _normalized_name(candidate.get("name_guess"))
    if not candidate_name or candidate_name == parent_name:
        return False
    return _has_recognized_item_header(_optional_text(candidate.get("header_line")))


def _known_repair_range_matches(parent: dict[str, object], candidate_first_page: int) -> bool:
    parent_name = _normalized_name(parent.get("name_guess"))
    if not parent_name:
        return False
    page_range = _KNOWN_REPAIR_RANGES.get(parent_name)
    if page_range is None:
        return False
    start_page, end_page = page_range
    return start_page <= candidate_first_page <= end_page


def _is_suspicious_continuation_candidate(item: dict[str, object]) -> bool:
    header_line = _optional_text(item.get("header_line"))
    if not header_line:
        return True

    normalized_header = _normalize_text(header_line)
    if any(normalized_header.startswith(value) for value in _SUSPICIOUS_HEADER_STARTS):
        return True
    if not _has_recognized_item_header(header_line):
        return True
    return False


def _has_recognized_item_header(header_line: str | None) -> bool:
    if not header_line:
        return False
    normalized_header = _normalize_text(header_line)
    return any(normalized_header.startswith(value) for value in _RECOGNIZED_HEADER_STARTS)


def _has_continuation_start(item: dict[str, object]) -> bool:
    raw_text = _optional_text(item.get("raw_text"))
    if not raw_text:
        return True
    normalized_text = _normalize_text(raw_text)
    if any(normalized_text.startswith(value) for value in _CONTINUATION_STARTS):
        return True
    return any(normalized_text.startswith(value) for value in _SUSPICIOUS_HEADER_STARTS)


def _is_likely_long_form_item(item: dict[str, object]) -> bool:
    header_line = _optional_text(item.get("header_line")) or ""
    raw_text = _optional_text(item.get("raw_text")) or ""
    normalized = _normalize_text(f"{header_line} {raw_text}")
    fragments = _fragments_from_item(item)

    return (
        "artifact" in normalized
        or len(raw_text) > 900
        or len(_source_pages_from_item(item)) > 1
        or any(fragment.get("continues_to_next_page") is True for fragment in fragments)
        or any(
            phrase in normalized
            for phrase in (
                "ages past",
                "crafted eons",
                "centuries ago",
                "orc horde",
                "elven lands",
                "foreign land",
                "village",
            )
        )
    )


def _merge_assembled_item(
    parent: dict[str, object],
    candidate: dict[str, object],
) -> dict[str, object]:
    merged = copy.deepcopy(parent)
    child_name = _optional_text(candidate.get("name_guess"))
    child_header = _optional_text(candidate.get("header_line"))

    if _optional_text(merged.get("name_guess")) is None and child_name is not None:
        merged["name_guess"] = child_name
    if _optional_text(merged.get("header_line")) is None and child_header is not None:
        merged["header_line"] = child_header

    fragments = _fragments_from_item(parent) + _fragments_from_item(candidate)
    merged["fragments"] = copy.deepcopy(fragments)
    merged_pages = sorted(
        set(
            _source_pages_from_item(parent)
            + _source_pages_from_item(candidate)
            + _source_pages_from_fragments(fragments)
        )
    )
    if len(merged_pages) > 1:
        merged_pages = list(range(min(merged_pages), max(merged_pages) + 1))
    merged["source_pages"] = merged_pages

    merged["raw_text"] = "\n\n".join(_raw_text_parts(parent) + _raw_text_parts(candidate))
    merged["needs_review"] = True
    merged["warnings"] = _warnings_from_item(parent)
    _add_warnings(
        merged,
        _warnings_from_item(candidate)
        + [
            (
                "Repair: merged continuation record "
                f"{child_name or '(unnamed)'} from page(s) "
                f"{_source_pages_from_item(candidate)} into "
                f"{_optional_text(parent.get('name_guess')) or '(unnamed parent)'}."
            )
        ],
    )
    return merged


def _source_pages_from_item(item: object) -> list[int]:
    if not isinstance(item, dict):
        return []

    pages = item.get("source_pages")
    if isinstance(pages, list):
        valid_pages = [
            page for page in pages if isinstance(page, int) and not isinstance(page, bool) and page > 0
        ]
        if valid_pages:
            return sorted(set(valid_pages))

    return _source_pages_from_fragments(_fragments_from_item(item))


def _source_pages_from_fragments(fragments: list[dict[str, object]]) -> list[int]:
    pages: list[int] = []
    for fragment in fragments:
        page = fragment.get("page_number")
        if isinstance(page, int) and not isinstance(page, bool) and page > 0:
            pages.append(page)
    return sorted(set(pages))


def _first_source_page(item: object) -> int:
    pages = _source_pages_from_item(item)
    if not pages:
        return 10**9
    return min(pages)


def _fragments_from_item(item: dict[str, object]) -> list[dict[str, object]]:
    fragments = item.get("fragments")
    if isinstance(fragments, list):
        return [copy.deepcopy(fragment) for fragment in fragments if isinstance(fragment, dict)]

    declared_pages = item.get("source_pages")
    page = 0
    if isinstance(declared_pages, list):
        valid_pages = [
            value
            for value in declared_pages
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        ]
        if valid_pages:
            page = min(valid_pages)
    fragment: dict[str, object] = {
        "page_number": page,
        "name": _optional_text(item.get("name_guess")),
        "header_line": _optional_text(item.get("header_line")),
        "raw_text": _optional_text(item.get("raw_text")) or "",
        "continues_from_previous_page": False,
        "continues_to_next_page": False,
    }
    return [fragment]


def _raw_text_parts(item: dict[str, object]) -> list[str]:
    raw_text = _optional_text(item.get("raw_text"))
    if raw_text:
        return [raw_text]

    parts: list[str] = []
    for fragment in _fragments_from_item(item):
        text = _optional_text(fragment.get("raw_text"))
        if text:
            parts.append(text)
    return parts


def _warnings_from_item(item: dict[str, object]) -> list[str]:
    warnings = item.get("warnings")
    if not isinstance(warnings, list):
        return []
    return [text for warning in warnings if (text := str(warning).strip())]


def _mentions_repair_or_continuation(warning: object) -> bool:
    normalized = _normalize_text(str(warning))
    return "repair" in normalized or "continuation" in normalized or "continues" in normalized


def _normalized_name(value: object) -> str:
    text = _optional_text(value)
    if text is None:
        return ""
    return _normalize_text(text)


def _normalize_text(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _delete_stale_assembled_items(output_dir: Path, saved_count: int) -> None:
    pattern = re.compile(r"^item_(\d+)\.json$")
    for path in output_dir.glob("item_*.json"):
        match = pattern.match(path.name)
        if match is None or int(match.group(1)) <= saved_count:
            continue
        _validate_output_path(path, output_dir)
        try:
            path.unlink()
        except Exception as exc:
            raise AssemblyError(f"Failed to delete stale assembled item JSON at {path}.") from exc


def _validate_page_extraction(page: dict[str, object]) -> dict[str, Any]:
    if not isinstance(page, dict):
        raise AssemblyError("Page extraction must be a JSON object.")

    page_number = page.get("page_number")
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise AssemblyError("Page extraction missing valid page_number.")

    items = page.get("items")
    if not isinstance(items, list):
        raise AssemblyError(f"Page {page_number}: items must be a list.")

    warnings = page.get("warnings", [])
    if not isinstance(warnings, list):
        raise AssemblyError(f"Page {page_number}: warnings must be a list.")

    for item in items:
        if not isinstance(item, dict):
            raise AssemblyError(f"Page {page_number}: item entry must be an object.")
        _usable_text(item.get("raw_text"), page_number)

    return {"page_number": page_number, "items": items, "warnings": warnings}


def _item_to_fragment(item: dict[str, object], page_number: int) -> dict[str, object]:
    fragment: dict[str, object] = {
        "page_number": page_number,
        "name": _optional_text(item.get("name")),
        "header_line": _optional_text(item.get("header_line")),
        "raw_text": _usable_text(item.get("raw_text"), page_number),
        "continues_from_previous_page": item.get("continues_from_previous_page") is True,
        "continues_to_next_page": item.get("continues_to_next_page") is True,
    }
    if "confidence" in item:
        fragment["confidence"] = item["confidence"]
    return fragment


def _new_assembled_item(
    fragment: dict[str, object],
    warnings: list[str],
) -> dict[str, object]:
    item = {
        "name_guess": _optional_text(fragment.get("name")),
        "header_line": _optional_text(fragment.get("header_line")),
        "raw_text": str(fragment["raw_text"]),
        "source_pages": [fragment["page_number"]],
        "needs_review": False,
        "warnings": [],
        "fragments": [copy.deepcopy(fragment)],
    }
    _add_warnings(item, warnings)
    return item


def _append_fragment(
    item: dict[str, object],
    fragment: dict[str, object],
    warnings: list[str],
) -> None:
    fragments = _fragments(item)
    fragments.append(copy.deepcopy(fragment))
    item["fragments"] = fragments
    item["raw_text"] = "\n\n".join(str(part["raw_text"]) for part in fragments)

    if item["name_guess"] is None:
        item["name_guess"] = _optional_text(fragment.get("name"))
    if item["header_line"] is None:
        item["header_line"] = _optional_text(fragment.get("header_line"))

    pages = {page for page in item["source_pages"] if isinstance(page, int)}
    pages.add(int(fragment["page_number"]))
    item["source_pages"] = sorted(pages)
    _add_warnings(item, warnings)


def _finalize_item(item: dict[str, object], assembled: list[dict[str, object]]) -> None:
    item["fragments"] = copy.deepcopy(_fragments(item))
    item["source_pages"] = sorted({page for page in item["source_pages"] if isinstance(page, int)})
    assembled.append(copy.deepcopy(item))


def _fragments(item: dict[str, object]) -> list[dict[str, object]]:
    fragments = item.get("fragments")
    if not isinstance(fragments, list):
        raise AssemblyError("Internal assembly error: fragments must be a list.")
    return [fragment for fragment in fragments if isinstance(fragment, dict)]


def _last_fragment_page(item: dict[str, object]) -> int | None:
    fragments = _fragments(item)
    if not fragments:
        return None
    page_number = fragments[-1].get("page_number")
    return page_number if isinstance(page_number, int) else None


def _mark_needs_review(item: dict[str, object], warning: str) -> None:
    item["needs_review"] = True
    _add_warnings(item, [warning])


def _add_warnings(item: dict[str, object], warnings: list[str]) -> None:
    existing = item.get("warnings")
    if not isinstance(existing, list):
        existing = []

    for warning in warnings:
        if warning and warning not in existing:
            existing.append(warning)
    item["warnings"] = existing


def _normalize_page_warnings(warnings: list[object], page_number: int) -> list[str]:
    normalized: list[str] = []
    for warning in warnings:
        text = str(warning).strip()
        if text:
            normalized.append(f"Page {page_number}: {text}")
    return normalized


def _usable_text(value: object, page_number: int) -> str:
    if value is None:
        raise AssemblyError(f"Page {page_number}: item raw_text is missing.")
    text = str(value)
    if not text.strip():
        raise AssemblyError(f"Page {page_number}: item raw_text is empty.")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validate_output_path(path: Path, output_dir: Path) -> None:
    resolved_output_dir = output_dir.resolve(strict=True)
    resolved_path = path.resolve(strict=False)
    if resolved_path.parent != resolved_output_dir:
        raise AssemblyError(f"Refusing to write outside output_dir: {resolved_path}")
    if path.is_symlink():
        raise AssemblyError(f"Refusing to write to symlinked output file: {path}")
    if path.exists() and path.is_dir():
        raise AssemblyError(f"Output path is a directory: {path}")


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    raise TypeError


__all__ = [
    "AssemblyError",
    "assemble_items",
    "assemble_items_from_config",
    "assembled_item_path",
    "find_suspicious_assembled_items",
    "load_assembled_item",
    "repair_assembled_items",
    "save_assembled_items",
]
