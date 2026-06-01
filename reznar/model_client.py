"""Vision model client boundary for Reznar page extraction."""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
from typing import Protocol

from openai import OpenAI

PAGE_EXTRACTION_PROMPT = """
You are reading one page image from a fantasy magic item catalog.

Extract only visible magic item blocks from this page. Preserve raw text as
faithfully as possible, including item names, header lines, rules text, and
visible continuation text. Do not invent missing text. If text is ambiguous,
damaged, cropped, or uncertain, include a warning.

Use continues_from_previous_page when a visible entry appears to continue from a
previous page. Use continues_to_next_page when a visible entry appears to
continue onto a later page.

Return only valid JSON with this exact top-level shape:
{
  "page_number": 1,
  "items": [
    {
      "name": "...",
      "header_line": "...",
      "raw_text": "...",
      "continues_from_previous_page": false,
      "continues_to_next_page": false,
      "confidence": 0.0
    }
  ],
  "warnings": []
}
""".strip()


class ModelClientError(Exception):
    """Raised when model extraction or response parsing fails."""


class VisionExtractor(Protocol):
    """Interface for page-level vision extraction."""

    def extract_page(self, image_path: Path, page_number: int) -> dict[str, object]:
        """Extract raw page data from one rendered page image."""


class MockVisionExtractor:
    """Deterministic extractor for tests and dry runs."""

    def __init__(self, sample_data: dict[str, object] | None = None) -> None:
        self._sample_data = copy.deepcopy(sample_data)

    def extract_page(self, image_path: Path, page_number: int) -> dict[str, object]:
        _require_image_file(image_path)

        if self._sample_data is None:
            result: dict[str, object] = {
                "page_number": page_number,
                "items": [
                    {
                        "name": "Mock Item",
                        "header_line": "Mock Item, wondrous item, common",
                        "raw_text": "Mock extraction for deterministic pipeline tests.",
                        "continues_from_previous_page": False,
                        "continues_to_next_page": False,
                        "confidence": 1.0,
                    }
                ],
                "warnings": [],
            }
        else:
            result = copy.deepcopy(self._sample_data)
            result["page_number"] = page_number

        _validate_raw_page_shape(result)
        return result


class OpenAIVisionExtractor:
    """OpenAI-backed extractor for rendered page PNGs."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        if not api_key.strip():
            raise ModelClientError("OpenAI API key is required.")
        if not model.strip():
            raise ModelClientError("OpenAI vision model is required.")
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
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> OpenAIVisionExtractor:
        """Build an extractor from a PipelineConfig-like object."""

        api_key = getattr(config, "openai_api_key", None)
        model = getattr(config, "openai_vision_model", None)
        if not isinstance(api_key, str) or not api_key.strip():
            raise ModelClientError("config.openai_api_key is required.")
        if not isinstance(model, str) or not model.strip():
            raise ModelClientError("config.openai_vision_model is required.")
        return cls(api_key=api_key, model=model, timeout=timeout, max_retries=max_retries)

    def extract_page(self, image_path: Path, page_number: int) -> dict[str, object]:
        image_file = _require_image_file(image_path)
        data_url = image_to_data_url(image_file)
        response_text = self._call_openai(data_url=data_url, page_number=page_number)
        result = parse_json_object(response_text)
        _validate_raw_page_shape(result)
        return result

    def _call_openai(self, *, data_url: str, page_number: int) -> str:
        user_text = (
            f"Extract the visible magic item blocks from page {page_number}. "
            "Return only the JSON object. Use the provided page_number value."
        )

        last_error: Exception | None = None
        for _attempt in range(self.max_retries + 1):
            try:
                response = self._client.responses.create(
                    model=self.model,
                    instructions=PAGE_EXTRACTION_PROMPT,
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": user_text},
                                {"type": "input_image", "image_url": data_url},
                            ],
                        }
                    ],
                    text={"format": {"type": "json_object"}},
                    timeout=self.timeout,
                )
                return _response_output_text(response)
            except Exception as exc:
                last_error = exc

        raise ModelClientError("OpenAI API call failed after retries.") from last_error


def encode_image_base64(image_path: Path) -> str:
    """Read an image file and return base64-encoded ASCII text."""

    image_file = _require_image_file(image_path)
    return base64.b64encode(image_file.read_bytes()).decode("ascii")


def image_to_data_url(image_path: Path, mime_type: str = "image/png") -> str:
    """Return a data URL for an image file."""

    if not mime_type.strip():
        raise ValueError("mime_type must not be blank.")
    return f"data:{mime_type};base64,{encode_image_base64(image_path)}"


def parse_json_object(text: str) -> dict[str, object]:
    """Parse model text as a JSON object."""

    cleaned = _strip_json_code_fence(text.strip())
    if not cleaned:
        raise ModelClientError("Model response was empty.")

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ModelClientError("Model response was not valid JSON.") from exc

    if not isinstance(parsed, dict):
        raise ModelClientError("Model response JSON must be an object.")
    return parsed


def _require_image_file(image_path: Path) -> Path:
    path = Path(image_path).expanduser()
    if not path.exists():
        raise ModelClientError(f"Image path does not exist: {path}")
    if not path.is_file():
        raise ModelClientError(f"Image path is not a file: {path}")
    return path


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


def _validate_raw_page_shape(result: dict[str, object]) -> None:
    missing_keys = {"page_number", "items", "warnings"} - result.keys()
    if missing_keys:
        missing = ", ".join(sorted(missing_keys))
        raise ModelClientError(f"Model response missing required keys: {missing}")
    if not isinstance(result["items"], list):
        raise ModelClientError("Model response 'items' must be a list.")
    if not isinstance(result["warnings"], list):
        raise ModelClientError("Model response 'warnings' must be a list.")


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

    raise ModelClientError("OpenAI response did not include text output.")


__all__ = [
    "ModelClientError",
    "MockVisionExtractor",
    "OpenAIVisionExtractor",
    "PAGE_EXTRACTION_PROMPT",
    "VisionExtractor",
    "encode_image_base64",
    "image_to_data_url",
    "parse_json_object",
]
