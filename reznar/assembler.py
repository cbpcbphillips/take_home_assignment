"""Assemble raw page-level extractions into item-level records."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import orjson


class AssemblyError(Exception):
    """Raised when raw page extractions cannot be assembled safely."""


_JSON_OPTIONS = orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS


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
    "load_assembled_item",
    "save_assembled_items",
]
