"""Render Reznar PDF pages into deterministic PNG image artifacts."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import fitz

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class PageRenderResult:
    """Metadata for one rendered or reused page image."""

    page_number: int
    image_path: Path
    sha256: str
    width: int
    height: int
    render_dpi: int


class RenderConfig(Protocol):
    """Config attributes needed to render pages."""

    pdf_path: Path
    pages_dir: Path
    render_dpi: int
    image_format: str
    max_pages: int | None


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest for a file."""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_pdf_pages(
    pdf_path: Path,
    output_dir: Path,
    render_dpi: int = 300,
    image_format: str = "png",
    max_pages: int | None = None,
    force: bool = False,
) -> list[PageRenderResult]:
    """Render PDF pages into PNG files under output_dir."""

    source_pdf = Path(pdf_path).expanduser()
    if not source_pdf.exists():
        raise FileNotFoundError(f"PDF does not exist: {source_pdf}")
    if not source_pdf.is_file():
        raise ValueError(f"PDF path is not a file: {source_pdf}")

    _validate_render_dpi(render_dpi)
    normalized_format = image_format.lower().strip()
    if normalized_format != "png":
        raise ValueError("Only image_format='png' is supported.")
    _validate_max_pages(max_pages)

    target_dir = Path(output_dir).expanduser()
    _validate_output_dir(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    resolved_output_dir = target_dir.resolve(strict=True)

    results: list[PageRenderResult] = []
    with fitz.open(str(source_pdf)) as document:
        page_count = document.page_count if max_pages is None else min(document.page_count, max_pages)
        scale = render_dpi / 72
        matrix = fitz.Matrix(scale, scale)

        for page_index in range(page_count):
            page_number = page_index + 1
            image_path = target_dir / f"page_{page_number:03d}.png"
            resolved_image_path = _validate_output_file(image_path, resolved_output_dir)

            if force or not image_path.exists():
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                pixmap.save(str(image_path))

            width, height = _png_dimensions(image_path)
            results.append(
                PageRenderResult(
                    page_number=page_number,
                    image_path=resolved_image_path,
                    sha256=sha256_file(image_path),
                    width=width,
                    height=height,
                    render_dpi=render_dpi,
                )
            )

    return sorted(results, key=lambda result: result.page_number)


def render_pages_from_config(config: RenderConfig, force: bool = False) -> list[PageRenderResult]:
    """Render pages using a PipelineConfig-like object."""

    return render_pdf_pages(
        pdf_path=config.pdf_path,
        output_dir=config.pages_dir,
        render_dpi=config.render_dpi,
        image_format=config.image_format,
        max_pages=config.max_pages,
        force=force,
    )


def _validate_render_dpi(render_dpi: int) -> None:
    if not isinstance(render_dpi, int) or isinstance(render_dpi, bool) or render_dpi <= 0:
        raise ValueError("render_dpi must be a positive integer.")


def _validate_max_pages(max_pages: int | None) -> None:
    if max_pages is None:
        return
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages <= 0:
        raise ValueError("max_pages must be None or a positive integer.")


def _validate_output_dir(output_dir: Path) -> None:
    if output_dir.is_symlink():
        raise ValueError(f"Refusing to render into symlinked output_dir: {output_dir}")
    if output_dir.exists():
        if not output_dir.is_dir():
            raise ValueError(f"output_dir exists but is not a directory: {output_dir}")


def _validate_output_file(image_path: Path, output_dir: Path) -> Path:
    resolved_image_path = image_path.resolve(strict=False)
    if resolved_image_path.parent != output_dir:
        raise ValueError(f"Refusing to write outside output_dir: {resolved_image_path}")
    if image_path.is_symlink():
        raise ValueError(f"Refusing to write to symlinked output file: {image_path}")
    if image_path.exists() and not image_path.is_file():
        raise ValueError(f"Output path exists but is not a file: {image_path}")
    return resolved_image_path


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as file:
        header = file.read(24)

    if len(header) < 24 or header[:8] != _PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise ValueError(f"Rendered file is not a valid PNG: {path}")

    width, height = struct.unpack(">II", header[16:24])
    return width, height


__all__ = [
    "PageRenderResult",
    "RenderConfig",
    "render_pages_from_config",
    "render_pdf_pages",
    "sha256_file",
]
