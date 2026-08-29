"""Security behavior for HTTP-backed image analyzers."""

from io import BytesIO

from PIL import Image

from custom_components.samsung_frame_art_director.ai import create_analyzer


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


class _FakeSession:
    def __init__(self):
        self.url = None
        self.json = None
        self.headers = None
        self.timeout = None

    def post(self, url, *, json, headers, timeout):
        self.url = url
        self.json = json
        self.headers = headers
        self.timeout = timeout
        return _FakeGeminiResponse()


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
        gemini_api_key=secret,
        session=session,
    )
    assert error is None

    result = await analyzer.analyze_image(_png_bytes(), prompt="Describe this image")

    assert result["tags"] == ["calm", "blue", "abstract"]
    assert session.url.endswith(":generateContent")
    assert secret not in session.url
    assert session.headers == {"x-goog-api-key": secret}
    assert session.json["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"
    assert secret not in repr(analyzer.__dict__)
