"""AI Vision Engine for Samsung Frame Art Director.

This module abstracts the interaction with various AI providers (Gemini, OpenAI)
to analyze images and generate descriptive tags.
It is designed to be usable standalone (for testing) or within HA.
"""
import logging
import asyncio
import base64
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any

# Configure logging for standalone use
logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger(__name__)

class ImageAnalyzer(ABC):
    """Abstract base class for AI image analyzers."""

    def __init__(self, api_key: str, model_name: str = "default"):
        self.api_key = api_key
        self.model_name = model_name

    @abstractmethod
    async def analyze_image(self, image_bytes: bytes, prompt: str) -> dict:
        """Analyze image and return tags and metadata.
        
        Returns:
            dict: {
                "tags": list[str],
                "description": str,
                "provider": str,
                "model": str,
                "duration": float
            }
        """
        pass

class GeminiAnalyzer(ImageAnalyzer):
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model_name = model
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    async def analyze_image(self, image_data: bytes, prompt: str = "Describe this art") -> Dict[str, Any]:
        """Analyze image using Gemini Vision REST API."""
        import base64
        import aiohttp
        
        start_time = time.time()
        
        # Prepare structured prompt
        structured_prompt = (
            f"{prompt}\n"
            "Return exactly 15 descriptive keywords or short phrases separated by commas. "
            "Include visual style (e.g. oil painting), subject (e.g. mountains), "
            "and explicitly infer: Weather (e.g. sunny, rainy), Lighting (e.g. golden hour, dark), "
            "and Mood (e.g. calm, energetic). "
            "Example: landscape, mountains, sunny, clear sky, morning light, calm, nature, river, clouds, impressionism, bright, blue, summer, peaceful, outdoors"
        )

        # Prepare JSON payload
        b64_image = base64.b64encode(image_data).decode('utf-8')
        payload = {
            "contents": [{
                "parts": [
                    {"text": structured_prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg", # Assuming JPEG/PNG, API handles generic well usually, but we stick to jpeg/png
                            "data": b64_image
                        }
                    }
                ]
            }],
            "generationConfig": {
                "maxOutputTokens": 500,
                "temperature": 0.4
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.url, json=payload, timeout=30) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        _LOGGER.error(f"Gemini API Error {response.status}: {error_text}")
                        return {"error": f"API Error {response.status}"}
                    
                    data = await response.json()
                    
                    # Parse response
                    try:
                        # Candidate -> Content -> Parts -> Text
                        text = data['candidates'][0]['content']['parts'][0]['text']
                    except (KeyError, IndexError):
                        _LOGGER.error(f"Malformed Gemini Response: {data}")
                        return {"error": "Malformed Response"}
                    
                    # Process Tags
                    tags = [t.strip().lower() for t in text.split(',') if t.strip()]
                    duration = time.time() - start_time
                    
                    return {
                        "tags": tags[:15], # Limit to 15
                        "description": text,
                        "provider": "Google Gemini (REST)",
                        "model": self.model_name,
                        "duration": duration
                    }

        except Exception as e:
            _LOGGER.error(f"Gemini Request Failed: {e}")
            return {"error": str(e)}


class OpenAIAnalyzer(ImageAnalyzer):
    """OpenAI GPT-4o Vision Analyzer."""
    
    def __init__(self, api_key: str, model_name: str = "gpt-4o"):
        super().__init__(api_key, model_name)
        try:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=api_key)
        except ImportError:
            _LOGGER.error("openai package not installed")
            self._client = None

    async def analyze_image(self, image_bytes: bytes, prompt: str) -> dict:
        if not self._client:
            return {"error": "Dependency missing"}
            
        start_time = time.time()
        try:
            # OpenAI requires base64 encoded image
            b64_image = base64.b64encode(image_bytes).decode('utf-8')
            
            response = await self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64_image}"
                                }
                            },
                        ],
                    }
                ],
                max_tokens=300,
            )
            
            text = response.choices[0].message.content
            tags = [t.strip().lower() for t in text.replace("\n", ",").split(",") if t.strip()]
            
            duration = time.time() - start_time
            return {
                "tags": tags[:15],
                "description": text,
                "provider": "OpenAI",
                "model": self.model_name,
                "duration": round(duration, 3)
            }
            
        except Exception as e:
            _LOGGER.error("OpenAI analysis failed: %s", e)
            return {"error": str(e), "provider": "OpenAI"}
