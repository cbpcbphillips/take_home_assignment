"""Page extraction orchestration and raw JSON caching."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import orjson


class ExtractionError(Exception):
    """Raised when page extraction orchestration or raw JSON caching fails."""


class RenderedPage(Protocol):
    """Rendered page attributes needed for extraction."""

    page_number: int
    image_path: Path


class PageExtractor(Protocol):
    """Extractor interface used by the orchestration layer."""

    def extract_page(self, image_path: Path, page_number: int) -> dict[str, object]:
        """Extract raw page data from one rendered page image."""


@dataclass(frozen=True, slots=True)
class PageExtractionResult:
    """Raw extraction result for one rendered page."""

    page_number: int
    image_path: Path
    output_path: Path
    data: dict[str, object]
    from_cache: bool
    error: str | None = None
    succeeded: bool = True


def raw_extraction_path(raw_extractions_dir: Path, page_number: int) -> Path:
    """Return the deterministic raw JSON path for a 1-based page number."""

    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ExtractionError(f"Invalid page number {page_number}; expected a 1-based page number.")
    return Path(raw_extractions_dir) / f"page_{page_number:03d}.json"


def raw_extraction_error_path(raw_extractions_dir: Path, page_number: int) -> Path:
    """Return the deterministic page extraction error JSON path."""

    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ExtractionError(f"Invalid page number {page_number}; expected a 1-based page number.")
    return Path(raw_extractions_dir) / "errors" / f"page_{page_number:03d}_error.json"


def save_raw_extraction(path: Path, data: dict[str, object]) -> None:
    """Save one raw page extraction as stable UTF-8 JSON."""

    target_path = Path(path)
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        payload = orjson.dumps(
            copy.deepcopy(data),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
        )
        target_path.write_bytes(payload + b"\n")
    except Exception as exc:
        raise ExtractionError(f"Failed to save raw extraction JSON at {target_path}.") from exc


def load_raw_extraction(path: Path) -> dict[str, object]:
    """Load one raw page extraction JSON object."""

    source_path = Path(path)
    try:
        parsed = orjson.loads(source_path.read_bytes())
    except Exception as exc:
        raise ExtractionError(f"Failed to load raw extraction JSON at {source_path}.") from exc

    if not isinstance(parsed, dict):
        raise ExtractionError(f"Raw extraction JSON must contain an object: {source_path}")
    return parsed


def extract_pages(
    rendered_pages: list[RenderedPage],
    extractor: PageExtractor,
    raw_extractions_dir: Path,
    force: bool = False,
    continue_on_error: bool = False,
    request_delay_seconds: float = 0.0,
) -> list[PageExtractionResult]:
    """Extract raw page JSON for rendered pages, reusing cached JSON when possible."""

    if request_delay_seconds < 0:
        raise ExtractionError("request_delay_seconds must be non-negative.")

    output_dir = Path(raw_extractions_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise ExtractionError(f"Failed to create raw extraction directory: {output_dir}") from exc

    results: list[PageExtractionResult] = []
    for index, rendered_page in enumerate(rendered_pages):
        page_number = rendered_page.page_number
        image_path = Path(rendered_page.image_path)
        output_path = raw_extraction_path(output_dir, page_number)
        from_cache = False
        should_delay = False

        try:
            if output_path.exists() and not force:
                from_cache = True
                data = load_raw_extraction(output_path)
            else:
                if not image_path.exists() or not image_path.is_file():
                    raise ExtractionError(
                        f"Missing rendered image before extraction for page {page_number}: {image_path}"
                    )
                extracted = extractor.extract_page(image_path, page_number)
                should_delay = True
                if not isinstance(extracted, dict):
                    raise ExtractionError(f"Extractor returned non-dict data for page {page_number}.")
                data = extracted
                save_raw_extraction(output_path, data)

            results.append(
                PageExtractionResult(
                    page_number=page_number,
                    image_path=image_path,
                    output_path=output_path,
                    data=data,
                    from_cache=from_cache,
                )
            )
        except Exception as exc:
            if not continue_on_error:
                raise
            error_path = raw_extraction_error_path(output_dir, page_number)
            error_data = {
                "page_number": page_number,
                "image_path": str(image_path),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "from_cache": from_cache,
            }
            save_raw_extraction(error_path, error_data)
            results.append(
                PageExtractionResult(
                    page_number=page_number,
                    image_path=image_path,
                    output_path=error_path,
                    data={},
                    from_cache=from_cache,
                    error=str(exc),
                    succeeded=False,
                )
            )
            should_delay = not from_cache

        if should_delay and request_delay_seconds > 0 and index < len(rendered_pages) - 1:
            time.sleep(request_delay_seconds)

    return results


def extract_pages_from_config(
    config: object,
    rendered_pages: list[RenderedPage],
    extractor: PageExtractor,
    force: bool = False,
    continue_on_error: bool = False,
    request_delay_seconds: float = 0.0,
) -> list[PageExtractionResult]:
    """Extract pages using a PipelineConfig-like object."""

    return extract_pages(
        rendered_pages=rendered_pages,
        extractor=extractor,
        raw_extractions_dir=config.raw_extractions_dir,
        force=force,
        continue_on_error=continue_on_error,
        request_delay_seconds=request_delay_seconds,
    )


__all__ = [
    "ExtractionError",
    "PageExtractionResult",
    "extract_pages",
    "extract_pages_from_config",
    "load_raw_extraction",
    "raw_extraction_error_path",
    "raw_extraction_path",
    "save_raw_extraction",
]
