"""AI vision providers used to generate artwork tags."""

import base64
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientTimeout

from .const import (
    AI_PROVIDER_ANTHROPIC,
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_OPENAI,
    CONF_ANTHROPIC_API_KEY,
    CONF_ANTHROPIC_MODEL,
    CONF_GEMINI_API_KEY,
    CONF_GEMINI_MODEL,
    CONF_OPENAI_API_KEY,
    CONF_OPENAI_MODEL,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OPENAI_MODEL,
)

_LOGGER = logging.getLogger(__name__)

AI_REQUEST_ERROR = "AI provider request failed"
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_IMAGE_PIXELS = 40_000_000
DEFAULT_MAX_IMAGE_DIMENSION = 16_384
ANTHROPIC_MAX_IMAGE_BYTES = 7 * 1024 * 1024
ANTHROPIC_MAX_IMAGE_DIMENSION = 8_000


@dataclass(frozen=True, slots=True)
class AIProviderSpec:
    """Configuration and adapter metadata for one image-analysis provider."""

    key: str
    display_name: str
    credential_option: str
    model_option: str
    default_model: str
    analyzer_type: type["ImageAnalyzer"]


def detect_image_mime(image_bytes: bytes) -> str:
    """Return the supported image MIME type derived from its signature."""
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(image_bytes) >= 12 and image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Unsupported or invalid image format")


async def _async_post_provider_json(
    session: Any,
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    provider: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """POST one provider request with shared timeout and safe failures."""
    try:
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=ClientTimeout(total=30),
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                _LOGGER.warning(
                    "%s request returned HTTP %s",
                    provider,
                    response.status,
                )
                status = response.status
                if status == 429:
                    category = "rate_limited"
                elif status in (401, 403):
                    category = "authentication"
                elif status in (400, 404):
                    category = "configuration"
                elif status == 408 or status >= 500:
                    category = "unavailable"
                else:
                    category = "request"
                return None, {
                    "error": f"{AI_REQUEST_ERROR} (HTTP {response.status})",
                    "provider": provider,
                    "status": status,
                    "category": category,
                    "batch_fatal": category != "request",
                }
            return await response.json(), None
    except Exception:  # noqa: BLE001 - provider exceptions are untrusted
        _LOGGER.error("%s request failed", provider)
        return None, {
            "error": AI_REQUEST_ERROR,
            "provider": provider,
            "category": "unavailable",
            "batch_fatal": True,
        }


def _tagging_prompt(prompt: str) -> str:
    """Return the provider-independent artwork classification contract."""
    return (
        f"{prompt}\n"
        "Return only one JSON object with this exact shape: "
        '{"tags":["tag 1","tag 2"],"description":"one short sentence"}. '
        "The tags array must contain exactly 15 unique descriptive keywords or "
        "short phrases. Include visual style, subject, dominant colors, weather, "
        "lighting, mood, season, and setting when visible. Do not use Markdown."
    )


def _normalized_analysis(text: str) -> tuple[list[str], str]:
    """Normalize structured output, retaining legacy comma output as a fallback."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()

    tags_source: Any = None
    description = ""
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        tags_source = parsed.get("tags")
        raw_description = parsed.get("description", "")
        if isinstance(raw_description, str):
            description = raw_description.strip()
    if not isinstance(tags_source, list):
        tags_source = cleaned.replace("\n", ",").split(",")

    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in tags_source:
        if not isinstance(raw_tag, str):
            continue
        tag = raw_tag.strip(" \t\r\n-•.*\"'").lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag[:80])
        if len(tags) == 15:
            break
    if not tags:
        raise ValueError("Provider response contains no usable tags")
    return tags, description or cleaned


def _analysis_result(
    text: str,
    *,
    provider: str,
    model: str,
    start_time: float,
) -> dict[str, Any]:
    """Build the common successful provider result."""
    tags, description = _normalized_analysis(text)
    return {
        "tags": tags,
        "description": description,
        "provider": provider,
        "model": model,
        "duration": round(time.monotonic() - start_time, 3),
    }


class ImageAnalyzer(ABC):
    """Abstract base class for AI image analyzers."""

    def __init__(self, session: Any, model_name: str) -> None:
        self._session = session
        self.model_name = model_name

    max_image_bytes = DEFAULT_MAX_IMAGE_BYTES
    max_image_pixels = DEFAULT_MAX_IMAGE_PIXELS
    max_image_dimension = DEFAULT_MAX_IMAGE_DIMENSION

    @abstractmethod
    async def analyze_image(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        api_key: str,
    ) -> dict:
        """Analyze image bytes with a credential supplied only for this call."""


class GeminiAnalyzer(ImageAnalyzer):
    """Google Gemini REST analyzer using Home Assistant's HTTP session."""

    def __init__(self, session: Any, model: str = DEFAULT_GEMINI_MODEL) -> None:
        super().__init__(session, model)
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str = "Describe this art",
        *,
        api_key: str,
    ) -> dict[str, Any]:
        """Analyze an image using the Gemini Vision REST API."""
        start_time = time.monotonic()
        structured_prompt = _tagging_prompt(prompt)
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": structured_prompt},
                        {
                            "inline_data": {
                                "mime_type": detect_image_mime(image_data),
                                "data": base64.b64encode(image_data).decode("ascii"),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {"maxOutputTokens": 500, "temperature": 0.4},
        }

        provider = "Google Gemini (REST)"
        data, error = await _async_post_provider_json(
            self._session,
            self.url,
            payload=payload,
            headers={"x-goog-api-key": api_key},
            provider=provider,
        )
        if error:
            return error

        try:
            if data is not None:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
            if data is None or not isinstance(text, str):
                raise TypeError("Provider response text is not a string")
        except (KeyError, IndexError, TypeError):
            _LOGGER.warning("Gemini returned a malformed response")
            return {
                "error": "AI provider returned a malformed response",
                "provider": provider,
            }

        try:
            return _analysis_result(
                text,
                provider=provider,
                model=self.model_name,
                start_time=start_time,
            )
        except ValueError:
            return {
                "error": "AI provider returned no usable tags",
                "provider": provider,
            }


class OpenAIAnalyzer(ImageAnalyzer):
    """OpenAI REST analyzer using Home Assistant's HTTP session."""

    def __init__(self, session: Any, model_name: str = DEFAULT_OPENAI_MODEL) -> None:
        super().__init__(session, model_name)
        self.url = "https://api.openai.com/v1/chat/completions"

    async def analyze_image(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        api_key: str,
    ) -> dict:
        """Analyze an image using OpenAI's REST API."""
        start_time = time.monotonic()
        mime_type = detect_image_mime(image_bytes)
        image_data = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _tagging_prompt(prompt)},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{image_data}"},
                        },
                    ],
                }
            ],
            "max_completion_tokens": 500,
        }

        provider = "OpenAI"
        data, error = await _async_post_provider_json(
            self._session,
            self.url,
            payload=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            provider=provider,
        )
        if error:
            return error

        try:
            if data is not None:
                text = data["choices"][0]["message"]["content"]
            if data is None or not isinstance(text, str):
                raise TypeError("Provider response text is not a string")
        except (KeyError, IndexError, TypeError):
            _LOGGER.warning("OpenAI returned a malformed response")
            return {
                "error": "AI provider returned a malformed response",
                "provider": provider,
            }

        try:
            return _analysis_result(
                text,
                provider=provider,
                model=self.model_name,
                start_time=start_time,
            )
        except ValueError:
            return {
                "error": "AI provider returned no usable tags",
                "provider": provider,
            }


class AnthropicAnalyzer(ImageAnalyzer):
    """Anthropic Messages adapter using Home Assistant's HTTP session."""

    max_image_bytes = ANTHROPIC_MAX_IMAGE_BYTES
    max_image_dimension = ANTHROPIC_MAX_IMAGE_DIMENSION

    def __init__(
        self,
        session: Any,
        model_name: str = DEFAULT_ANTHROPIC_MODEL,
    ) -> None:
        super().__init__(session, model_name)
        self.url = "https://api.anthropic.com/v1/messages"

    async def analyze_image(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        api_key: str,
    ) -> dict[str, Any]:
        """Analyze one image through Anthropic's Messages vision interface."""
        start_time = time.monotonic()
        payload = {
            "model": self.model_name,
            "max_tokens": 500,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": detect_image_mime(image_bytes),
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": _tagging_prompt(prompt)},
                    ],
                }
            ],
        }
        provider = "Anthropic"
        data, error = await _async_post_provider_json(
            self._session,
            self.url,
            payload=payload,
            headers={
                "anthropic-version": "2023-06-01",
                "x-api-key": api_key,
            },
            provider=provider,
        )
        if error:
            return error
        try:
            content = data["content"] if data is not None else None
            text = next(
                block["text"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
        except (KeyError, StopIteration, TypeError):
            _LOGGER.warning("Anthropic returned a malformed response")
            return {
                "error": "AI provider returned a malformed response",
                "provider": provider,
            }
        try:
            return _analysis_result(
                text,
                provider=provider,
                model=self.model_name,
                start_time=start_time,
            )
        except ValueError:
            return {
                "error": "AI provider returned no usable tags",
                "provider": provider,
            }


AI_PROVIDER_SPECS: dict[str, AIProviderSpec] = {
    AI_PROVIDER_GEMINI: AIProviderSpec(
        AI_PROVIDER_GEMINI,
        "Google Gemini",
        CONF_GEMINI_API_KEY,
        CONF_GEMINI_MODEL,
        DEFAULT_GEMINI_MODEL,
        GeminiAnalyzer,
    ),
    AI_PROVIDER_OPENAI: AIProviderSpec(
        AI_PROVIDER_OPENAI,
        "OpenAI",
        CONF_OPENAI_API_KEY,
        CONF_OPENAI_MODEL,
        DEFAULT_OPENAI_MODEL,
        OpenAIAnalyzer,
    ),
    AI_PROVIDER_ANTHROPIC: AIProviderSpec(
        AI_PROVIDER_ANTHROPIC,
        "Anthropic",
        CONF_ANTHROPIC_API_KEY,
        CONF_ANTHROPIC_MODEL,
        DEFAULT_ANTHROPIC_MODEL,
        AnthropicAnalyzer,
    ),
}


def get_provider_spec(provider: str) -> AIProviderSpec | None:
    """Return metadata for one explicitly supported provider."""
    return AI_PROVIDER_SPECS.get((provider or "").strip().lower())


def create_analyzer(
    provider: str,
    model: str = "",
    *,
    session: Any | None = None,
) -> tuple[ImageAnalyzer | None, str | None]:
    """Build the configured analyzer without accepting or storing API keys."""
    provider = (provider or AI_PROVIDER_GEMINI).strip().lower()
    model = (model or "").strip()
    if session is None:
        return None, "AI HTTP session is unavailable."
    spec = get_provider_spec(provider)
    if spec is None:
        return None, f"Unsupported AI provider: {provider}"
    return spec.analyzer_type(session, model or spec.default_model), None
