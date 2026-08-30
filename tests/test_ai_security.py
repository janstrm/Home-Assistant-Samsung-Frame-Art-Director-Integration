"""Security and provider-contract behavior for HTTP-backed image analyzers."""

import asyncio
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from custom_components.samsung_frame_art_director.ai import (
    AI_REQUEST_ERROR,
    create_analyzer,
    detect_image_mime,
)
from custom_components.samsung_frame_art_director.curator import ContentCurator


class _FakeGeminiResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "calm, blue, abstract"}]}}]}


class _FakeOpenAIResponse(_FakeGeminiResponse):
    async def json(self):
        return {"choices": [{"message": {"content": "calm, blue, abstract"}}]}


class _FakeAnthropicResponse(_FakeGeminiResponse):
    async def json(self):
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "tags": ["calm", "blue", "abstract"],
                            "description": "A calm blue abstract artwork.",
                        }
                    ),
                }
            ]
        }


class _FakeStatusResponse(_FakeGeminiResponse):
    def __init__(self, status: int):
        self.status = status


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


@pytest.mark.parametrize(
    ("image_bytes", "expected"),
    [
        (b"\xff\xd8\xffjpeg", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\npng", "image/png"),
        (b"RIFF\x04\x00\x00\x00WEBP", "image/webp"),
    ],
)
def test_detect_image_mime_supports_all_provider_formats(image_bytes, expected):
    """JPEG, PNG and WebP signatures map to their actual MIME types."""
    assert detect_image_mime(image_bytes) == expected


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


async def test_process_inbox_binds_provider_and_key_once(hass):
    """An options reload cannot cross provider key and model settings."""
    inbox = Path(hass.config.path("www", "bound-key-inbox"))
    library = Path(hass.config.path("www", "bound-key-library"))
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "art.png").write_bytes(_png_bytes())
    entry = SimpleNamespace(
        options={
            "inbox_dir": str(inbox),
            "library_dir": str(library),
            "ai_provider": "gemini",
            "gemini_api_key": "gemini-key",
            "gemini_model": "gemini-user-model",
            "openai_api_key": "openai-key",
            "openai_model": "openai-user-model",
        }
    )
    api = SimpleNamespace(async_add_local_art=AsyncMock())
    curator = ContentCurator(hass, entry, api)
    analyzer = SimpleNamespace(analyze_image=AsyncMock(return_value={"tags": ["safe"]}))

    captured_configuration = {}

    def _create_and_reload_options(provider, *, model, session):
        captured_configuration.update(
            {"provider": provider, "model": model, "session": session}
        )
        entry.options["ai_provider"] = "openai"
        return analyzer, None

    with (
        patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=object(),
        ),
        patch(
            "custom_components.samsung_frame_art_director.curator.create_analyzer",
            side_effect=_create_and_reload_options,
        ),
    ):
        result = await curator.async_process_inbox()

    assert result["count"] == 1
    assert analyzer.analyze_image.await_args.kwargs["api_key"] == "gemini-key"
    assert captured_configuration["provider"] == "gemini"
    assert captured_configuration["model"] == "gemini-user-model"


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


@pytest.mark.parametrize(
    ("status", "category"),
    [(401, "authentication"), (404, "configuration"), (429, "rate_limited"), (503, "unavailable")],
)
async def test_provider_wide_http_errors_stop_the_batch(status, category):
    """Provider-wide HTTP failures carry machine-readable batch semantics."""
    analyzer, error = create_analyzer(
        "gemini",
        session=_FakeSession(_FakeStatusResponse(status)),
    )
    assert error is None

    result = await analyzer.analyze_image(
        _png_bytes(),
        prompt="Describe this image",
        api_key="key",
    )

    assert result["status"] == status
    assert result["category"] == category
    assert result["batch_fatal"] is True


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


async def test_anthropic_uses_messages_vision_contract_without_persisting_key():
    """Anthropic receives a base64 image and returns the shared normalized result."""
    session = _FakeSession(_FakeAnthropicResponse())
    secret = "super-secret-anthropic-key"
    analyzer, error = create_analyzer(
        "anthropic",
        model="claude-haiku-4-5-20251001",
        session=session,
    )
    assert error is None

    result = await analyzer.analyze_image(
        _png_bytes(),
        prompt="Describe this image",
        api_key=secret,
    )

    assert result["tags"] == ["calm", "blue", "abstract"]
    assert result["description"] == "A calm blue abstract artwork."
    assert session.url == "https://api.anthropic.com/v1/messages"
    assert session.headers == {
        "anthropic-version": "2023-06-01",
        "x-api-key": secret,
    }
    assert session.json["model"] == "claude-haiku-4-5-20251001"
    source = session.json["messages"][0]["content"][0]["source"]
    assert source["media_type"] == "image/png"
    assert secret not in repr(analyzer.__dict__)


async def test_anthropic_rejects_non_object_content_blocks_cleanly():
    """Unexpected Anthropic content items become a safe provider error."""
    response = _FakeAnthropicResponse()
    response.json = AsyncMock(return_value={"content": [None, "text"]})
    analyzer, error = create_analyzer(
        "anthropic",
        session=_FakeSession(response),
    )
    assert error is None

    result = await analyzer.analyze_image(
        _png_bytes(),
        prompt="Describe this image",
        api_key="key",
    )

    assert result == {
        "error": "AI provider returned a malformed response",
        "provider": "Anthropic",
    }


async def test_openai_receives_the_same_structured_tag_contract_as_gemini():
    """Every provider is asked for tags and a description in one common shape."""
    session = _FakeSession(_FakeOpenAIResponse())
    analyzer, error = create_analyzer("openai", session=session)
    assert error is None

    await analyzer.analyze_image(
        _png_bytes(),
        prompt="Describe this image",
        api_key="key",
    )

    prompt = session.json["messages"][0]["content"][0]["text"]
    assert '"tags"' in prompt
    assert '"description"' in prompt
    assert "exactly 15" in prompt


async def test_parallel_inbox_runs_analyze_each_file_only_once(hass):
    """A shared library lock prevents duplicate provider calls and move races."""
    inbox = Path(hass.config.path("www", "parallel-inbox"))
    library = Path(hass.config.path("www", "parallel-library"))
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "art.png").write_bytes(_png_bytes())
    entry = SimpleNamespace(options={"inbox_dir": str(inbox), "library_dir": str(library)})
    api = SimpleNamespace(async_add_local_art=AsyncMock())
    first = ContentCurator(hass, entry, api)
    second = ContentCurator(hass, entry, api)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def _analyze(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        first_started.set()
        await release_first.wait()
        return {"tags": ["safe"], "description": "Safe"}

    analyzer = SimpleNamespace(analyze_image=_analyze)
    first._build_analyzer = lambda: (analyzer, "key", None)
    second._build_analyzer = lambda: (analyzer, "key", None)

    first_task = asyncio.create_task(first.async_process_inbox())
    await first_started.wait()
    second_task = asyncio.create_task(second.async_process_inbox())
    await asyncio.sleep(0)
    release_first.set()
    results = await asyncio.gather(first_task, second_task)

    assert calls == 1
    assert sum(result["count"] for result in results) == 1


async def test_batch_fatal_provider_error_stops_after_first_image(hass):
    """Authentication/model failures do not repeat for every inbox image."""
    inbox = Path(hass.config.path("www", "fatal-inbox"))
    library = Path(hass.config.path("www", "fatal-library"))
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "first.png").write_bytes(_png_bytes())
    (inbox / "second.png").write_bytes(_png_bytes())
    entry = SimpleNamespace(options={"inbox_dir": str(inbox), "library_dir": str(library)})
    api = SimpleNamespace(async_add_local_art=AsyncMock())
    curator = ContentCurator(hass, entry, api)
    analyzer = SimpleNamespace(
        analyze_image=AsyncMock(
            return_value={
                "error": "AI provider authentication failed",
                "batch_fatal": True,
            }
        )
    )
    curator._build_analyzer = lambda: (analyzer, "key", None)

    result = await curator.async_process_inbox()

    assert result["count"] == 0
    analyzer.analyze_image.assert_awaited_once()
    assert len(list(inbox.iterdir())) == 2
