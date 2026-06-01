"""Configuration for the Reznar PDF extraction pipeline."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_DEFAULT_PDF_PATH = "data/items_combined.pdf"
_DEFAULT_OUTPUT_DIR = "data"
_DEFAULT_RENDER_DPI = "300"
_DEFAULT_IMAGE_FORMAT = "png"
_DEFAULT_OPENAI_MODEL = "gpt-5.5"
_DEFAULT_DRY_RUN = "false"
_DEFAULT_LOG_LEVEL = "INFO"

_TRUTHY_VALUES = {"1", "true", "yes", "y", "on"}
_FALSEY_VALUES = {"0", "false", "no", "n", "off", ""}


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Resolved runtime configuration for the extraction pipeline."""

    pdf_path: Path
    output_dir: Path
    pages_dir: Path
    raw_extractions_dir: Path
    assembled_dir: Path
    validated_dir: Path
    reports_dir: Path
    render_dpi: int
    image_format: str
    openai_api_key: str | None
    openai_vision_model: str
    openai_mapper_model: str
    max_pages: int | None
    dry_run: bool
    log_level: str


def load_config(
    *,
    env_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    project_root: str | Path | None = None,
) -> PipelineConfig:
    """Load pipeline configuration from `.env` and process environment values."""

    root = _PROJECT_ROOT if project_root is None else Path(project_root).resolve()
    dotenv_path = root / ".env" if env_path is None else _resolve_path(env_path, root)
    load_dotenv(dotenv_path=dotenv_path, override=False)

    env = os.environ if environ is None else environ

    pdf_path = _resolve_path(_env_text(env, "REZNAR_PDF_PATH", _DEFAULT_PDF_PATH), root)
    output_dir = _resolve_path(_env_text(env, "REZNAR_OUTPUT_DIR", _DEFAULT_OUTPUT_DIR), root)

    image_format = _env_text(env, "REZNAR_IMAGE_FORMAT", _DEFAULT_IMAGE_FORMAT).lower()
    if image_format != "png":
        raise ValueError("REZNAR_IMAGE_FORMAT must be 'png'.")

    render_dpi = _positive_int(
        _env_text(env, "REZNAR_RENDER_DPI", _DEFAULT_RENDER_DPI),
        "REZNAR_RENDER_DPI",
    )
    max_pages = _optional_positive_int(env.get("REZNAR_MAX_PAGES"), "REZNAR_MAX_PAGES")

    dry_run = _bool_value(_env_text(env, "REZNAR_DRY_RUN", _DEFAULT_DRY_RUN), "REZNAR_DRY_RUN")

    return PipelineConfig(
        pdf_path=pdf_path,
        output_dir=output_dir,
        pages_dir=output_dir / "pages",
        raw_extractions_dir=output_dir / "raw_extractions",
        assembled_dir=output_dir / "assembled",
        validated_dir=output_dir / "validated",
        reports_dir=output_dir / "reports",
        render_dpi=render_dpi,
        image_format=image_format,
        openai_api_key=_optional_text(env.get("OPENAI_API_KEY")),
        openai_vision_model=_env_text(env, "OPENAI_VISION_MODEL", _DEFAULT_OPENAI_MODEL),
        openai_mapper_model=_env_text(env, "OPENAI_MAPPER_MODEL", _DEFAULT_OPENAI_MODEL),
        max_pages=max_pages,
        dry_run=dry_run,
        log_level=_env_text(env, "REZNAR_LOG_LEVEL", _DEFAULT_LOG_LEVEL).upper(),
    )


def ensure_output_dirs(config: PipelineConfig) -> list[Path]:
    """Create generated pipeline output directories and return them."""

    directories = [
        config.pages_dir,
        config.raw_extractions_dir,
        config.assembled_dir,
        config.validated_dir,
        config.reports_dir,
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    return directories


def _resolve_path(path: str | Path, project_root: Path) -> Path:
    configured_path = Path(path).expanduser()
    if configured_path.is_absolute():
        return configured_path.resolve()
    return (project_root / configured_path).resolve()


def _env_text(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped if stripped else default


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _positive_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return parsed


def _optional_positive_int(value: str | None, name: str) -> int | None:
    normalized = _optional_text(value)
    if normalized is None:
        return None
    return _positive_int(normalized, name)


def _bool_value(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUTHY_VALUES:
        return True
    if normalized in _FALSEY_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean value.")


__all__ = ["PipelineConfig", "ensure_output_dirs", "load_config"]
