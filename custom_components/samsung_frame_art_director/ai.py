"""AI vision providers used to generate artwork tags."""

from abc import ABC, abstractmethod
import base64
import logging
import time
from typing import Any

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


def _analysis_result(
    text: str,
    *,
    provider: str,
    model: str,
    start_time: float,
) -> dict[str, Any]:
    """Build the common successful provider result."""
    tags = [
        tag.strip().lower()
        for tag in text.replace("\n", ",").split(",")
        if tag.strip()
    ]
    return {
        "tags": tags[:15],
        "description": text,
        "provider": provider,
        "model": model,
        "duration": round(time.monotonic() - start_time, 3),
    }


class ImageAnalyzer(ABC):
    """Abstract base class for AI image analyzers."""

    def __init__(self, session: Any, model_name: str) -> None:
        self._session = session
        self.model_name = model_name

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

    def __init__(self, session: Any, model: str = "gemini-2.5-flash") -> None:
        super().__init__(session, model)
        self.url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str = "Describe this art",
        *,
        api_key: str,
    ) -> dict[str, Any]:
        """Analyze an image using the Gemini Vision REST API."""
        start_time = time.monotonic()
        structured_prompt = (
            f"{prompt}\n"
            "Return exactly 15 descriptive keywords or short phrases separated by commas. "
            "Include visual style (e.g. oil painting), subject (e.g. mountains), "
            "and explicitly infer: Weather (e.g. sunny, rainy), "
            "Lighting (e.g. golden hour, dark), and Mood (e.g. calm, energetic). "
            "Example: landscape, mountains, sunny, clear sky, morning light, calm, "
            "nature, river, clouds, impressionism, bright, blue, summer, peaceful, outdoors"
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
            async with self._session.post(
                self.url,
                json=payload,
                headers={"x-goog-api-key": api_key},
                timeout=ClientTimeout(total=30),
                allow_redirects=False,
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
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                if not isinstance(text, str):
                    raise TypeError("Provider response text is not a string")
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

        return _analysis_result(
            text,
            provider="Google Gemini (REST)",
            model=self.model_name,
            start_time=start_time,
        )


class OpenAIAnalyzer(ImageAnalyzer):
    """OpenAI REST analyzer using Home Assistant's HTTP session."""

    def __init__(self, session: Any, model_name: str = "gpt-4o") -> None:
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
            "max_tokens": 300,
        }

        try:
            async with self._session.post(
                self.url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=ClientTimeout(total=30),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    _LOGGER.warning(
                        "OpenAI request returned HTTP %s",
                        response.status,
                    )
                    return {
                        "error": f"{AI_REQUEST_ERROR} (HTTP {response.status})",
                        "provider": "OpenAI",
                    }
                data = await response.json()
                text = data["choices"][0]["message"]["content"]
                if not isinstance(text, str):
                    raise TypeError("Provider response text is not a string")
        except (KeyError, IndexError, TypeError):
            _LOGGER.warning("OpenAI returned a malformed response")
            return {
                "error": "AI provider returned a malformed response",
                "provider": "OpenAI",
            }
        except Exception:  # noqa: BLE001 - provider exceptions are untrusted
            _LOGGER.error("OpenAI request failed")
            return {"error": AI_REQUEST_ERROR, "provider": "OpenAI"}

        return _analysis_result(
            text,
            provider="OpenAI",
            model=self.model_name,
            start_time=start_time,
        )


def create_analyzer(
    provider: str,
    model: str = "",
    *,
    session: Any | None = None,
) -> tuple[ImageAnalyzer | None, str | None]:
    """Build the configured analyzer without accepting or storing API keys."""
    provider = (provider or "gemini").lower()
    model = (model or "").strip()
    if session is None:
        return None, "AI HTTP session is unavailable."
    if provider == "openai":
        return OpenAIAnalyzer(session, model or "gpt-4o"), None
    return GeminiAnalyzer(session, model or "gemini-2.5-flash"), None
