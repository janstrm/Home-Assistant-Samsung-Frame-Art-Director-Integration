"""AI vision providers used to generate artwork tags."""

from abc import ABC, abstractmethod
import base64
import logging
import time
from typing import Any, Callable

from aiohttp import ClientTimeout

_LOGGER = logging.getLogger(__name__)

AI_REQUEST_ERROR = "AI provider request failed"


def detect_image_mime(image_bytes: bytes) -> str:
    """Return the supported image MIME type derived from its signature."""
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if (
        len(image_bytes) >= 12
        and image_bytes.startswith(b"RIFF")
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    raise ValueError("Unsupported or invalid image format")


class ImageAnalyzer(ABC):
    """Abstract base class for AI image analyzers."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    @abstractmethod
    async def analyze_image(self, image_bytes: bytes, prompt: str) -> dict:
        """Analyze image bytes and return tags and metadata."""


class GeminiAnalyzer(ImageAnalyzer):
    """Google Gemini REST analyzer using an injected HTTP request boundary."""

    def __init__(
        self,
        post_request: Callable[..., Any],
        model: str = "gemini-2.5-flash",
    ) -> None:
        super().__init__(model)
        self._post_request = post_request
        self.url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str = "Describe this art",
    ) -> dict[str, Any]:
        """Analyze an image using the Gemini Vision REST API."""
        start_time = time.monotonic()
        structured_prompt = (
            f"{prompt}\n"
            "Return exactly 15 descriptive keywords or short phrases separated by commas. "
            "Include visual style, subject, weather, lighting, and mood."
        )
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

        try:
            async with self._post_request(
                self.url,
                json=payload,
                timeout=ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    _LOGGER.warning(
                        "Gemini request returned HTTP %s",
                        response.status,
                    )
                    return {
                        "error": f"{AI_REQUEST_ERROR} (HTTP {response.status})",
                        "provider": "Google Gemini (REST)",
                    }

                data = await response.json()
                try:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError, TypeError):
                    _LOGGER.warning("Gemini returned a malformed response")
                    return {
                        "error": "AI provider returned a malformed response",
                        "provider": "Google Gemini (REST)",
                    }
        except Exception:  # noqa: BLE001 - provider exceptions are untrusted
            _LOGGER.error("Gemini request failed")
            return {
                "error": AI_REQUEST_ERROR,
                "provider": "Google Gemini (REST)",
            }

        tags = [tag.strip().lower() for tag in text.split(",") if tag.strip()]
        return {
            "tags": tags[:15],
            "description": text,
            "provider": "Google Gemini (REST)",
            "model": self.model_name,
            "duration": time.monotonic() - start_time,
        }


class OpenAIAnalyzer(ImageAnalyzer):
    """OpenAI vision analyzer using an injected SDK request boundary."""

    def __init__(
        self,
        create_completion: Callable[..., Any],
        model_name: str = "gpt-4o",
    ) -> None:
        super().__init__(model_name)
        self._create_completion = create_completion

    async def analyze_image(self, image_bytes: bytes, prompt: str) -> dict:
        """Analyze an image using OpenAI's chat completions API."""
        start_time = time.monotonic()
        mime_type = detect_image_mime(image_bytes)
        image_data = base64.b64encode(image_bytes).decode("ascii")

        try:
            response = await self._create_completion(
                model=self.model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_data}"
                                },
                            },
                        ],
                    }
                ],
                max_tokens=300,
            )
            text = response.choices[0].message.content
        except Exception:  # noqa: BLE001 - provider exceptions are untrusted
            _LOGGER.error("OpenAI request failed")
            return {"error": AI_REQUEST_ERROR, "provider": "OpenAI"}

        tags = [
            tag.strip().lower()
            for tag in text.replace("\n", ",").split(",")
            if tag.strip()
        ]
        return {
            "tags": tags[:15],
            "description": text,
            "provider": "OpenAI",
            "model": self.model_name,
            "duration": round(time.monotonic() - start_time, 3),
        }


def create_analyzer(
    provider: str,
    gemini_api_key: str = "",
    openai_api_key: str = "",
    model: str = "",
    *,
    session: Any | None = None,
) -> tuple[ImageAnalyzer | None, str | None]:
    """Build the configured analyzer without persisting raw API keys on it."""
    provider = (provider or "gemini").lower()
    model = (model or "").strip()

    if provider == "openai":
        if not openai_api_key:
            return None, (
                "OpenAI selected but no OpenAI API key configured. "
                "Add it in Settings > Devices > Samsung Frame Art Director > Configure."
            )
        try:
            from openai import AsyncOpenAI
        except ImportError:
            return None, "OpenAI selected but the openai package is not installed."

        client = AsyncOpenAI(api_key=openai_api_key)

        async def _create_completion(**kwargs):
            return await client.chat.completions.create(**kwargs)

        return OpenAIAnalyzer(_create_completion, model or "gpt-4o"), None

    if not gemini_api_key:
        return None, (
            "No Gemini API key configured. "
            "Add it in Settings > Devices > Samsung Frame Art Director > Configure."
        )
    if session is None:
        return None, "Gemini HTTP session is unavailable."

    def _post_request(url: str, **kwargs):
        return session.post(
            url,
            headers={"x-goog-api-key": gemini_api_key},
            **kwargs,
        )

    return GeminiAnalyzer(_post_request, model or "gemini-2.5-flash"), None
