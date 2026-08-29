"""Security behavior for HTTP-backed image analyzers."""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from custom_components.samsung_frame_art_director.ai import (
    AI_REQUEST_ERROR,
    create_analyzer,
)
from custom_components.samsung_frame_art_director.curator import ContentCurator


class _FakeGeminiResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def json(self):
        return {
            "candidates": [
                {"content": {"parts": [{"text": "calm, blue, abstract"}]}}
            ]
        }


class _FakeOpenAIResponse(_FakeGeminiResponse):
    async def json(self):
        return {"choices": [{"message": {"content": "calm, blue, abstract"}}]}


class _FakeSession:
    def __init__(self, response=None):
        self.response = response or _FakeGeminiResponse()
        self.url = None
        self.json = None
        self.headers = None
        self.timeout = None
        self.allow_redirects = None

    def post(self, url, *, json, headers, timeout, allow_redirects):
        self.url = url
        self.json = json
        self.headers = headers
        self.timeout = timeout
        self.allow_redirects = allow_redirects
        return self.response


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buffer, "PNG")
    return buffer.getvalue()


async def test_gemini_uses_injected_session_without_persisting_key():
    """Gemini sends a detected MIME type and keeps credentials out of its URL/state."""
    session = _FakeSession()
    secret = "super-secret-gemini-key"

    analyzer, error = create_analyzer(
        "gemini",
        session=session,
    )
    assert error is None

    result = await analyzer.analyze_image(
        _png_bytes(),
        prompt="Describe this image",
        api_key=secret,
    )

    assert result["tags"] == ["calm", "blue", "abstract"]
    assert session.url.endswith(":generateContent")
    assert secret not in session.url
    assert session.headers == {"x-goog-api-key": secret}
    assert session.allow_redirects is False
    assert session.json["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"
    assert secret not in repr(analyzer.__dict__)
    assert not any(callable(value) for value in analyzer.__dict__.values())


async def test_process_inbox_uses_home_assistant_shared_session(hass):
    """The public inbox action wires Gemini through HA's shared HTTP client."""
    inbox = Path(hass.config.path("www", "ai-session-inbox"))
    library = Path(hass.config.path("www", "ai-session-library"))
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "art.png").write_bytes(_png_bytes())
    entry = SimpleNamespace(
        options={
            "inbox_dir": str(inbox),
            "library_dir": str(library),
            "ai_provider": "gemini",
            "gemini_api_key": "shared-session-key",
        }
    )
    api = SimpleNamespace(async_add_local_art=AsyncMock())
    curator = ContentCurator(hass, entry, api)
    session = _FakeSession()

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        return_value=session,
    ):
        result = await curator.async_process_inbox()

    assert result["count"] == 1
    assert session.url.endswith(":generateContent")
    api.async_add_local_art.assert_awaited_once()


async def test_gemini_provider_failure_does_not_expose_key(caplog):
    """Untrusted provider exception text is neither returned nor logged."""
    secret = "never-log-this-key"

    class _FailingSession:
        def post(self, *args, **kwargs):
            raise RuntimeError(f"request failed with key={secret}")

    analyzer, error = create_analyzer(
        "gemini",
        session=_FailingSession(),
    )
    assert error is None

    result = await analyzer.analyze_image(
        _png_bytes(),
        prompt="Describe this image",
        api_key=secret,
    )

    assert result["error"] == AI_REQUEST_ERROR
    assert secret not in str(result)
    assert secret not in caplog.text


async def test_openai_uses_detected_png_mime_type():
    """OpenAI uses HA HTTP and describes the actual image format."""
    session = _FakeSession(_FakeOpenAIResponse())
    secret = "super-secret-openai-key"
    analyzer, error = create_analyzer(
        "openai",
        session=session,
    )
    assert error is None

    result = await analyzer.analyze_image(
        _png_bytes(),
        prompt="Describe this image",
        api_key=secret,
    )

    assert result["tags"] == ["calm", "blue", "abstract"]
    assert session.url == "https://api.openai.com/v1/chat/completions"
    assert session.headers == {"Authorization": f"Bearer {secret}"}
    assert session.allow_redirects is False
    data_uri = session.json["messages"][0]["content"][1]["image_url"]["url"]
    assert data_uri.startswith("data:image/png;base64,")
    assert secret not in repr(analyzer.__dict__)
    assert not any(callable(value) for value in analyzer.__dict__.values())
