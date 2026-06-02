"""Typer CLI orchestration for the Reznar extraction pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from reznar import cleanup, loader, repository
from reznar.assembler import (
    assemble_items_from_config,
    find_suspicious_assembled_items,
    load_assembled_item,
    repair_assembled_items,
    save_assembled_items,
)
from reznar.config import PipelineConfig, ensure_output_dirs, load_config
from reznar.extractor import (
    PageExtractionResult,
    extract_pages_from_config,
    load_raw_extraction,
)
from reznar.mapper import (
    DeterministicFallbackMapper,
    OntologyMapper,
    OpenAIOntologyMapper,
    map_assembled_items_with_mapper,
)
from reznar.model_client import MockVisionExtractor, OpenAIVisionExtractor, VisionExtractor
from reznar.render_pages import PageRenderResult, render_pages_from_config
from reznar.validator import ValidationResult, validate_mapped_item

PIPELINE_VERSION = "module-11"

app = typer.Typer(help="Reznar PDF extraction pipeline.")
console = Console()


@dataclass(frozen=True, slots=True)
class MappingValidationRun:
    validation_results: list[ValidationResult]
    mapper_fallback_count: int
    mapping_mode: str
    real_mapper: bool


@app.command("init-db")
def init_db_command() -> None:
    """Create repository tables and print counts."""

    try:
        repository.init_db()
        _print_counts(repository.list_table_counts())
    except Exception as exc:
        _exit_with_error(exc)


@app.command("counts")
def counts_command() -> None:
    """Print repository table row counts."""

    try:
        _print_counts(repository.list_table_counts())
    except Exception as exc:
        _exit_with_error(exc)


@app.command("render")
def render_command(
    max_pages: Annotated[int | None, typer.Option("--max-pages", min=1)] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Render PDF pages into PNG files."""

    try:
        config = _config_with_max_pages(load_config(), max_pages)
        ensure_output_dirs(config)
        rendered_pages = render_pages_from_config(config, force=force)
        console.print(
            f"Rendered/reused [bold]{len(rendered_pages)}[/] page(s) in {config.pages_dir}"
        )
    except Exception as exc:
        _exit_with_error(exc)


@app.command("extract")
def extract_command(
    max_pages: Annotated[int | None, typer.Option("--max-pages", min=1)] = None,
    force_render: Annotated[bool, typer.Option("--force-render")] = False,
    force_extract: Annotated[bool, typer.Option("--force-extract")] = False,
    real_api: Annotated[bool, typer.Option("--real-api")] = False,
    continue_on_error: Annotated[
        bool | None,
        typer.Option("--continue-on-error/--no-continue-on-error"),
    ] = None,
    request_delay_seconds: Annotated[
        float | None,
        typer.Option("--request-delay-seconds"),
    ] = None,
) -> None:
    """Render pages, then extract raw page JSON using a mock extractor by default."""

    try:
        config = _config_with_max_pages(load_config(), max_pages)
        continue_after_page_errors = _resolve_continue_on_error(real_api, continue_on_error)
        request_delay = _resolve_request_delay_seconds(real_api, request_delay_seconds)
        ensure_output_dirs(config)
        rendered_pages = render_pages_from_config(config, force=force_render)
        vision_extractor = _make_vision_extractor(config, real_api=real_api)
        if real_api:
            console.print("Real API extraction is running sequentially with caching.")
        page_results = extract_pages_from_config(
            config,
            rendered_pages,
            vision_extractor,
            force=force_extract,
            continue_on_error=continue_after_page_errors,
            request_delay_seconds=request_delay,
        )
        _print_extract_summary(
            attempted_pages=len(rendered_pages),
            page_results=page_results,
            raw_extractions_dir=config.raw_extractions_dir,
        )
    except Exception as exc:
        _exit_with_error(exc)


@app.command("assemble")
def assemble_command(
    force: Annotated[bool, typer.Option("--force")] = False,
    repair_assembly: Annotated[
        bool,
        typer.Option("--repair-assembly/--no-repair-assembly"),
    ] = True,
) -> None:
    """Assemble raw page extractions into item-level records."""

    try:
        config = load_config()
        page_extractions = _load_page_extractions(config)
        initial_assembled_items = assemble_items_from_config(
            config,
            page_extractions,
            save=False,
            force=force,
        )
        assembled_items = _repair_assembled_items_if_enabled(
            initial_assembled_items,
            repair_assembly=repair_assembly,
        )
        save_assembled_items(assembled_items, config.assembled_dir, force=force)
        _print_assembly_repair_summary(
            initial_count=len(initial_assembled_items),
            assembled_items=assembled_items,
        )
        console.print(
            f"Assembled [bold]{len(assembled_items)}[/] item(s) in {config.assembled_dir}"
        )
    except Exception as exc:
        _exit_with_error(exc)


@app.command("validate")
def validate_command(
    store: Annotated[bool, typer.Option("--store")] = False,
    repair_assembly: Annotated[
        bool,
        typer.Option("--repair-assembly/--no-repair-assembly"),
    ] = True,
    real_mapper: Annotated[bool, typer.Option("--real-mapper/--no-real-mapper")] = False,
    mapper_delay_seconds: Annotated[float, typer.Option("--mapper-delay-seconds")] = 1.0,
    fallback_on_mapper_error: Annotated[
        bool,
        typer.Option("--fallback-on-mapper-error/--no-fallback-on-mapper-error"),
    ] = True,
) -> None:
    """Map assembled item JSON and validate against the ontology."""

    try:
        config = load_config()
        _validate_mapper_delay_seconds(mapper_delay_seconds)
        initial_assembled_items = _load_assembled_items(config)
        assembled_items = _repair_assembled_items_if_enabled(
            initial_assembled_items,
            repair_assembly=repair_assembly,
        )
        _print_assembly_repair_summary(
            initial_count=len(initial_assembled_items),
            assembled_items=assembled_items,
        )
        mapping_run = _map_and_validate_items(
            config,
            assembled_items,
            real_mapper=real_mapper,
            mapper_delay_seconds=mapper_delay_seconds,
            fallback_on_mapper_error=fallback_on_mapper_error,
        )
        summary = _summarize_validation_results(mapping_run.validation_results)

        if store:
            repository.init_db()
            for validation_result in mapping_run.validation_results:
                repository.insert_validation_result(None, validation_result)

        _print_mapping_summary(
            mapping_run=mapping_run,
            assembled_item_count=len(assembled_items),
        )
        console.print(f"Assembled items: [bold]{len(assembled_items)}[/]")
        console.print(f"Valid entities: [bold]{summary['valid_entities']}[/]")
        console.print(f"Validation errors: [bold]{summary['validation_errors']}[/]")
        if store:
            console.print("Stored validation staging records.")
    except Exception as exc:
        _exit_with_error(exc)


@app.command("load")
def load_command(
    repair_assembly: Annotated[
        bool,
        typer.Option("--repair-assembly/--no-repair-assembly"),
    ] = True,
    real_mapper: Annotated[bool, typer.Option("--real-mapper/--no-real-mapper")] = False,
    mapper_delay_seconds: Annotated[float, typer.Option("--mapper-delay-seconds")] = 1.0,
    fallback_on_mapper_error: Annotated[
        bool,
        typer.Option("--fallback-on-mapper-error/--no-fallback-on-mapper-error"),
    ] = True,
) -> None:
    """Validate assembled items and load valid entities into canonical tables."""

    try:
        config = load_config()
        _validate_mapper_delay_seconds(mapper_delay_seconds)
        initial_assembled_items = _load_assembled_items(config)
        assembled_items = _repair_assembled_items_if_enabled(
            initial_assembled_items,
            repair_assembly=repair_assembly,
        )
        _print_assembly_repair_summary(
            initial_count=len(initial_assembled_items),
            assembled_items=assembled_items,
        )
        mapping_run = _map_and_validate_items(
            config,
            assembled_items,
            real_mapper=real_mapper,
            mapper_delay_seconds=mapper_delay_seconds,
            fallback_on_mapper_error=fallback_on_mapper_error,
        )
        repository.init_db()
        load_results = [
            loader.load_validation_result(result) for result in mapping_run.validation_results
        ]
        _print_mapping_summary(
            mapping_run=mapping_run,
            assembled_item_count=len(assembled_items),
        )
        _print_load_summary(load_results)
        _print_real_mapper_related_warning(mapping_run, load_results)
    except Exception as exc:
        _exit_with_error(exc)


@app.command("run")
def run_command(
    max_pages: Annotated[int | None, typer.Option("--max-pages", min=1)] = None,
    real_api: Annotated[bool, typer.Option("--real-api")] = False,
    force_render: Annotated[bool, typer.Option("--force-render")] = False,
    force_extract: Annotated[bool, typer.Option("--force-extract")] = False,
    force_assemble: Annotated[bool, typer.Option("--force-assemble")] = False,
    skip_db: Annotated[bool, typer.Option("--skip-db")] = False,
    repair_assembly: Annotated[
        bool,
        typer.Option("--repair-assembly/--no-repair-assembly"),
    ] = True,
    real_mapper: Annotated[bool, typer.Option("--real-mapper/--no-real-mapper")] = False,
    mapper_delay_seconds: Annotated[float, typer.Option("--mapper-delay-seconds")] = 1.0,
    fallback_on_mapper_error: Annotated[
        bool,
        typer.Option("--fallback-on-mapper-error/--no-fallback-on-mapper-error"),
    ] = True,
    continue_on_error: Annotated[
        bool | None,
        typer.Option("--continue-on-error/--no-continue-on-error"),
    ] = None,
    request_delay_seconds: Annotated[
        float | None,
        typer.Option("--request-delay-seconds"),
    ] = None,
) -> None:
    """Run the full local pipeline."""

    run_id: str | None = None
    try:
        config = _config_with_max_pages(load_config(), max_pages)
        continue_after_page_errors = _resolve_continue_on_error(real_api, continue_on_error)
        request_delay = _resolve_request_delay_seconds(real_api, request_delay_seconds)
        _validate_mapper_delay_seconds(mapper_delay_seconds)
        ensure_output_dirs(config)

        if not skip_db:
            repository.init_db()
            run_id = repository.create_extraction_run(
                config.pdf_path,
                model_name=config.openai_vision_model if real_api else "mock",
                pipeline_version=PIPELINE_VERSION,
            )

        rendered_pages = render_pages_from_config(config, force=force_render)
        if not skip_db:
            repository.insert_document_pages(run_id, rendered_pages)

        vision_extractor = _make_vision_extractor(config, real_api=real_api)
        if real_api:
            console.print("Real API extraction is running sequentially with caching.")
        page_results = extract_pages_from_config(
            config,
            rendered_pages,
            vision_extractor,
            force=force_extract,
            continue_on_error=continue_after_page_errors,
            request_delay_seconds=request_delay,
        )
        successful_page_results = _successful_page_results(page_results)
        if not skip_db:
            repository.insert_page_extractions_raw(run_id, successful_page_results)

        page_extractions = [result.data for result in successful_page_results]
        initial_assembled_items = assemble_items_from_config(
            config,
            page_extractions,
            save=False,
            force=force_assemble,
        )
        assembled_items = _repair_assembled_items_if_enabled(
            initial_assembled_items,
            repair_assembly=repair_assembly,
        )
        save_assembled_items(assembled_items, config.assembled_dir, force=force_assemble)
        if not skip_db:
            repository.insert_assembled_items_raw(run_id, assembled_items)

        mapping_run = _map_and_validate_items(
            config,
            assembled_items,
            real_mapper=real_mapper,
            mapper_delay_seconds=mapper_delay_seconds,
            fallback_on_mapper_error=fallback_on_mapper_error,
        )
        load_results: list[loader.LoadResult] = []
        if not skip_db:
            for validation_result in mapping_run.validation_results:
                repository.insert_validation_result(run_id, validation_result)
                load_results.append(loader.load_validation_result(validation_result))
            repository.finish_extraction_run(run_id, status="completed")

        _print_run_summary(
            rendered_pages=rendered_pages,
            page_results=page_results,
            assembled_items=assembled_items,
            mapping_run=mapping_run,
            load_results=load_results,
            skip_db=skip_db,
            error_dir=config.raw_extractions_dir / "errors",
            initial_assembled_count=len(initial_assembled_items),
        )
    except Exception as exc:
        if run_id is not None and not skip_db:
            try:
                repository.finish_extraction_run(run_id, status="failed", notes=str(exc))
            except Exception as finish_exc:
                console.print(f"[yellow]Could not mark extraction run failed: {finish_exc}[/]")
        _exit_with_error(exc)


@app.command("clean-artifacts")
def clean_artifacts_command() -> None:
    """Delete generated artifact directories."""

    try:
        deleted = cleanup.clean_artifacts(load_config())
        _print_deleted_paths(deleted)
    except Exception as exc:
        _exit_with_error(exc)


@app.command("clean-db")
def clean_db_command() -> None:
    """Delete the embedded local Postgres data directory."""

    try:
        deleted = cleanup.clean_db(load_config())
        _print_deleted_paths(deleted)
    except Exception as exc:
        _exit_with_error(exc)


@app.command("clean-all")
def clean_all_command(
    include_source_pdfs: Annotated[bool, typer.Option("--include-source-pdfs")] = False,
) -> None:
    """Delete generated artifacts, database files, and optionally source PDFs."""

    try:
        if include_source_pdfs:
            console.print("[bold red]Source PDF deletion is enabled for this cleanup.[/]")
        deleted = cleanup.clean_all(load_config(), include_source_pdfs=include_source_pdfs)
        _print_deleted_paths(deleted)
    except Exception as exc:
        _exit_with_error(exc)


def _load_page_extractions(config: PipelineConfig) -> list[dict[str, object]]:
    files = _numbered_json_files(config.raw_extractions_dir, prefix="page")
    if not files:
        _exit_with_message(
            f"No raw extraction files found in {config.raw_extractions_dir}. Run extract first."
        )
    return [load_raw_extraction(path) for path in files]


def _load_assembled_items(config: PipelineConfig) -> list[dict[str, object]]:
    files = _numbered_json_files(config.assembled_dir, prefix="item")
    if not files:
        _exit_with_message(f"No assembled item files found in {config.assembled_dir}. Run assemble first.")
    return [load_assembled_item(path) for path in files]


def _numbered_json_files(directory: Path, *, prefix: str) -> list[Path]:
    if not directory.exists():
        return []

    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)\.json$")

    def sort_key(path: Path) -> int:
        match = pattern.match(path.name)
        if match is None:
            raise typer.BadParameter(f"Unexpected {prefix} JSON filename: {path.name}")
        return int(match.group(1))

    files = list(directory.glob(f"{prefix}_*.json"))
    return sorted(files, key=sort_key)


def _config_with_max_pages(config: PipelineConfig, max_pages: int | None) -> PipelineConfig:
    if max_pages is None:
        return config
    if max_pages < 1:
        raise typer.BadParameter("--max-pages must be a positive integer.")
    return replace(config, max_pages=max_pages)


def _make_vision_extractor(config: PipelineConfig, *, real_api: bool) -> VisionExtractor:
    if real_api:
        return OpenAIVisionExtractor.from_config(config)
    return MockVisionExtractor()


def _make_ontology_mapper(config: PipelineConfig, *, real_mapper: bool) -> OntologyMapper:
    if real_mapper:
        return OpenAIOntologyMapper.from_config(config)
    return DeterministicFallbackMapper()


def _repair_assembled_items_if_enabled(
    assembled_items: list[dict[str, object]],
    *,
    repair_assembly: bool,
) -> list[dict[str, object]]:
    if not repair_assembly:
        return assembled_items
    return repair_assembled_items(assembled_items)


def _print_assembly_repair_summary(
    *,
    initial_count: int,
    assembled_items: list[dict[str, object]],
) -> None:
    suspicious_items = find_suspicious_assembled_items(assembled_items)
    console.print(f"Initial assembled item count: [bold]{initial_count}[/]")
    console.print(f"Repaired assembled item count: [bold]{len(assembled_items)}[/]")
    console.print(f"Multi-page assembled items: [bold]{_multi_page_item_count(assembled_items)}[/]")
    console.print(f"Suspicious assembled items: [bold]{len(suspicious_items)}[/]")


def _multi_page_item_count(assembled_items: list[dict[str, object]]) -> int:
    return sum(
        1
        for item in assembled_items
        if isinstance(item, dict) and len(_source_pages(item.get("source_pages"))) > 1
    )


def _source_pages(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [
        page for page in value if isinstance(page, int) and not isinstance(page, bool) and page > 0
    ]


def _resolve_continue_on_error(real_api: bool, value: bool | None) -> bool:
    if value is not None:
        return value
    return real_api


def _resolve_request_delay_seconds(real_api: bool, value: float | None) -> float:
    delay = (2.0 if real_api else 0.0) if value is None else value
    if delay < 0:
        raise typer.BadParameter("--request-delay-seconds must be non-negative.")
    return delay


def _validate_mapper_delay_seconds(value: float) -> None:
    if value < 0:
        raise typer.BadParameter("--mapper-delay-seconds must be non-negative.")


def _successful_page_results(
    page_results: list[PageExtractionResult],
) -> list[PageExtractionResult]:
    return [result for result in page_results if result.succeeded]


def _print_extract_summary(
    *,
    attempted_pages: int,
    page_results: list[PageExtractionResult],
    raw_extractions_dir: Path,
) -> None:
    successful = _successful_page_results(page_results)
    cached = sum(1 for result in successful if result.from_cache)
    failed = len(page_results) - len(successful)
    fresh = len(successful) - cached

    console.print(f"Pages attempted: [bold]{attempted_pages}[/]")
    console.print(f"Successful extractions: [bold]{len(successful)}[/]")
    console.print(f"New successful extractions: [bold]{fresh}[/]")
    console.print(f"Cached extractions: [bold]{cached}[/]")
    console.print(f"Failed extractions: [bold]{failed}[/]")
    console.print(f"Raw extraction directory: {raw_extractions_dir}")
    if failed:
        console.print(f"[yellow]Page error JSON directory: {raw_extractions_dir / 'errors'}[/]")


def _map_and_validate_items(
    config: PipelineConfig,
    assembled_items: list[dict[str, object]],
    *,
    real_mapper: bool,
    mapper_delay_seconds: float,
    fallback_on_mapper_error: bool,
) -> MappingValidationRun:
    _validate_mapper_delay_seconds(mapper_delay_seconds)
    mapper = _make_ontology_mapper(config, real_mapper=real_mapper)
    fallback_mapper: OntologyMapper | None = None
    if real_mapper and fallback_on_mapper_error:
        fallback_mapper = DeterministicFallbackMapper()

    mapping_mode = "real mapper" if real_mapper else "deterministic"
    try:
        mapped_items = map_assembled_items_with_mapper(
            assembled_items,
            mapper,
            continue_on_error=True,
            fallback_mapper=fallback_mapper,
            delay_seconds=mapper_delay_seconds if real_mapper else 0.0,
        )
    except Exception as exc:
        raise typer.BadParameter(f"Failed to map assembled items: {exc}") from exc

    validation_results: list[ValidationResult] = []
    for index, mapped_item in enumerate(mapped_items, start=1):
        try:
            validation_results.append(validate_mapped_item(mapped_item))
        except Exception as exc:
            raise typer.BadParameter(f"Failed to map/validate assembled item {index}: {exc}") from exc
    return MappingValidationRun(
        validation_results=validation_results,
        mapper_fallback_count=_mapper_fallback_count(mapped_items),
        mapping_mode=mapping_mode,
        real_mapper=real_mapper,
    )


def _mapper_fallback_count(mapped_items: list[dict[str, object]]) -> int:
    fallback_warnings = {
        "AI ontology mapping failed; deterministic fallback used.",
        "AI related entity mapping failed; deterministic MagicItem only.",
    }
    count = 0
    for mapped_item in mapped_items:
        magic_item = mapped_item.get("MagicItem")
        if not isinstance(magic_item, dict):
            continue
        extraction = magic_item.get("extraction")
        if not isinstance(extraction, dict):
            continue
        warnings = extraction.get("warnings")
        if isinstance(warnings, list) and any(warning in warnings for warning in fallback_warnings):
            count += 1
    return count


def _print_mapping_summary(
    *,
    mapping_run: MappingValidationRun,
    assembled_item_count: int,
) -> None:
    summary = _summarize_validation_results(mapping_run.validation_results)
    console.print(f"Mapping mode: [bold]{mapping_run.mapping_mode}[/]")
    console.print(f"Assembled item count: [bold]{assembled_item_count}[/]")
    console.print(f"Mapper fallback count: [bold]{mapping_run.mapper_fallback_count}[/]")
    console.print(f"Valid entities: [bold]{summary['valid_entities']}[/]")
    console.print(f"Validation errors: [bold]{summary['validation_errors']}[/]")
    _print_type_counts(
        "Valid entities by type",
        _valid_entity_type_counts(mapping_run.validation_results),
    )
    _print_type_counts(
        "Validation errors by type",
        _validation_error_type_counts(mapping_run.validation_results),
    )


def _valid_entity_type_counts(results: list[ValidationResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        for entity in result.valid_entities:
            counts[entity.entity_type] = counts.get(entity.entity_type, 0) + 1
    return counts


def _validation_error_type_counts(results: list[ValidationResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        for error in result.errors:
            counts[error.entity_type] = counts.get(error.entity_type, 0) + 1
    return counts


def _print_type_counts(title: str, counts: dict[str, int]) -> None:
    if not counts:
        console.print(f"{title}: [bold]0[/]")
        return

    table = Table(title=title)
    table.add_column("Entity type")
    table.add_column("Count", justify="right")
    for entity_type, count in sorted(counts.items()):
        table.add_row(entity_type, str(count))
    console.print(table)


def _print_real_mapper_related_warning(
    mapping_run: MappingValidationRun,
    load_results: list[loader.LoadResult],
) -> None:
    if not mapping_run.real_mapper:
        return
    if _loaded_related_item_entity_count(load_results) == 0:
        console.print("[yellow]Real mapper produced no loadable related entities.[/]")


def _loaded_related_item_entity_count(load_results: list[loader.LoadResult]) -> int:
    return sum(len(result.item_entity_ids) for result in load_results)


def _summarize_validation_results(results: list[ValidationResult]) -> dict[str, int]:
    return {
        "valid_entities": sum(len(result.valid_entities) for result in results),
        "validation_errors": sum(len(result.errors) for result in results),
    }


def _print_counts(counts: dict[str, int]) -> None:
    table = Table(title="Repository table counts")
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    for table_name, row_count in counts.items():
        table.add_row(table_name, str(row_count))
    console.print(table)


def _print_load_summary(load_results: list[loader.LoadResult]) -> None:
    loaded = sum(1 for result in load_results if result.magic_item_id is not None)
    inserted = sum(1 for result in load_results if result.inserted_magic_item)
    updated = sum(1 for result in load_results if result.updated_magic_item)
    related = sum(len(result.item_entity_ids) for result in load_results)
    warnings = sum(len(result.warnings) for result in load_results)

    console.print(f"Loaded magic items: [bold]{loaded}[/]")
    console.print(f"Inserted magic items: [bold]{inserted}[/]")
    console.print(f"Updated magic items: [bold]{updated}[/]")
    console.print(f"Related item entities: [bold]{related}[/]")
    console.print(f"Warnings: [bold]{warnings}[/]")


def _print_run_summary(
    *,
    rendered_pages: list[PageRenderResult],
    page_results: list[PageExtractionResult],
    assembled_items: list[dict[str, object]],
    initial_assembled_count: int,
    mapping_run: MappingValidationRun,
    load_results: list[loader.LoadResult],
    skip_db: bool,
    error_dir: Path,
) -> None:
    successful_pages = _successful_page_results(page_results)
    cached_pages = sum(1 for result in successful_pages if result.from_cache)
    failed_pages = len(page_results) - len(successful_pages)
    console.print("[bold]Pipeline complete.[/]")
    console.print(f"Rendered/reused pages: [bold]{len(rendered_pages)}[/]")
    console.print(f"Successful raw pages: [bold]{len(successful_pages)}[/]")
    console.print(f"Raw extraction cache hits: [bold]{cached_pages}[/]")
    console.print(f"Failed raw pages: [bold]{failed_pages}[/]")
    _print_assembly_repair_summary(
        initial_count=initial_assembled_count,
        assembled_items=assembled_items,
    )
    _print_mapping_summary(
        mapping_run=mapping_run,
        assembled_item_count=len(assembled_items),
    )
    if skip_db:
        console.print("Database steps skipped.")
    else:
        _print_load_summary(load_results)
        _print_real_mapper_related_warning(mapping_run, load_results)
    if failed_pages:
        console.print(
            f"[yellow]Warning: {failed_pages} page(s) failed extraction. "
            f"Error JSON files are in {error_dir}[/]"
        )


def _print_deleted_paths(paths: list[Path]) -> None:
    if not paths:
        console.print("No matching paths were deleted.")
        return
    console.print("Deleted paths:")
    for path in paths:
        console.print(f"- {path}")


def _exit_with_error(exc: Exception) -> NoReturn:
    console.print(f"[bold red]Error:[/] {exc}")
    raise typer.Exit(1) from exc


def _exit_with_message(message: str) -> NoReturn:
    console.print(f"[bold red]Error:[/] {message}")
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
