"""Cleanup utilities for generated Reznar pipeline artifacts."""

from __future__ import annotations

import shutil
from pathlib import Path

from reznar.config import PipelineConfig

_PROTECTED_ENV_FILES = {".env", ".env.example"}


def clean_artifacts(config: PipelineConfig) -> list[Path]:
    """Delete generated extraction artifact directories."""

    output_dir = _resolve(config.output_dir)
    source_pdf = _resolve(config.pdf_path)
    artifact_dirs = [
        config.pages_dir,
        config.raw_extractions_dir,
        config.assembled_dir,
        config.validated_dir,
        config.reports_dir,
    ]

    deleted: list[Path] = []
    for directory in artifact_dirs:
        deleted_path = _delete_output_directory(
            directory,
            output_dir=output_dir,
            protected_paths=[source_pdf],
        )
        if deleted_path is not None:
            deleted.append(deleted_path)
    return deleted


def clean_db(config: PipelineConfig) -> list[Path]:
    """Delete the embedded local Postgres data directory without connecting to it."""

    output_dir = _resolve(config.output_dir)
    deleted_path = _delete_output_directory(
        output_dir / ".pg",
        output_dir=output_dir,
        protected_paths=[_resolve(config.pdf_path)],
    )
    return [] if deleted_path is None else [deleted_path]


def clean_all(config: PipelineConfig, include_source_pdfs: bool = False) -> list[Path]:
    """Delete generated artifacts, local database files, and optionally the source PDF."""

    deleted = clean_artifacts(config)
    deleted.extend(clean_db(config))

    if include_source_pdfs:
        deleted_path = _delete_file(config.pdf_path)
        if deleted_path is not None:
            deleted.append(deleted_path)

    return deleted


def _delete_output_directory(
    path: Path,
    *,
    output_dir: Path,
    protected_paths: list[Path],
) -> Path | None:
    configured_path = path.expanduser()
    resolved_path = _resolve(configured_path)
    _validate_output_child(resolved_path, output_dir)
    _validate_not_env_path(configured_path)
    _validate_not_env_path(resolved_path)
    _validate_not_parent_of_protected_path(resolved_path, protected_paths)

    if not configured_path.exists():
        return None
    if configured_path.is_symlink():
        raise ValueError(f"Refusing to delete symlinked directory: {configured_path}")
    if not configured_path.is_dir():
        raise ValueError(f"Cleanup target is not a directory: {resolved_path}")

    _validate_no_env_files_inside(configured_path)
    shutil.rmtree(configured_path)
    return resolved_path


def _delete_file(path: Path) -> Path | None:
    configured_path = path.expanduser()
    resolved_path = _resolve(configured_path)
    _validate_not_env_path(configured_path)
    _validate_not_env_path(resolved_path)

    if not configured_path.exists() or not configured_path.is_file():
        return None
    if configured_path.is_symlink():
        raise ValueError(f"Refusing to delete symlinked file: {configured_path}")

    configured_path.unlink()
    return resolved_path


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _validate_output_child(path: Path, output_dir: Path) -> None:
    if path == output_dir or not path.is_relative_to(output_dir):
        raise ValueError(f"Cleanup target must be inside output_dir: {path}")


def _validate_not_env_path(path: Path) -> None:
    if path.name.lower() in _PROTECTED_ENV_FILES:
        raise ValueError(f"Refusing to delete protected environment file: {path}")


def _validate_not_parent_of_protected_path(path: Path, protected_paths: list[Path]) -> None:
    for protected_path in protected_paths:
        if protected_path == path or protected_path.is_relative_to(path):
            raise ValueError(f"Cleanup target contains protected path: {protected_path}")


def _validate_no_env_files_inside(path: Path) -> None:
    for child in path.rglob("*"):
        if child.name.lower() in _PROTECTED_ENV_FILES:
            raise ValueError(f"Cleanup target contains protected environment file: {child}")


__all__ = ["clean_all", "clean_artifacts", "clean_db"]
