import logging
import httpx
from typing import Optional, Tuple
from app.config import settings

logger = logging.getLogger(__name__)

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"

class ElevenLabsVoiceService:
    def __init__(self):
        self.api_key = settings.ELEVENLABS_API_KEY
        self.voice_id = settings.ELEVENLABS_VOICE_ID or "21m00Tcm4TlvDq8ikWAM" # Rachel default

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key.strip()) > 5)

    async def text_to_speech(self, text: str) -> Tuple[Optional[bytes], str]:
        """
        Convert text to speech using ElevenLabs Text-to-Speech API.
        Returns: (audio_bytes, mime_type)
        """
        if not self.is_configured:
            logger.info("ElevenLabs API key not configured. Using client-side speech synthesis fallback.")
            return None, "audio/mpeg"

        url = f"{ELEVENLABS_BASE_URL}/text-to-speech/{self.voice_id}"
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.api_key
        }
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8
            }
        }

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 200:
                    return response.content, "audio/mpeg"
                else:
                    logger.error(f"ElevenLabs TTS error {response.status_code}: {response.text}")
                    return None, "audio/mpeg"
        except Exception as e:
            logger.error(f"Exception during ElevenLabs TTS call: {e}")
            return None, "audio/mpeg"

    async def speech_to_text(self, audio_bytes: bytes, filename: str = "audio.wav", content_type: Optional[str] = None) -> Optional[str]:
        """
        Convert speech to text using ElevenLabs Speech-to-Text API (or Whisper fallback).
        """
        if not self.is_configured:
            logger.info("ElevenLabs API key not configured for STT.")
            return None

        # Determine true MIME type to prevent 'corrupted audio' rejection
        if not content_type:
            lower = filename.lower()
            if lower.endswith(".webm"):
                content_type = "audio/webm"
            elif lower.endswith(".mp4") or lower.endswith(".m4a"):
                content_type = "audio/mp4"
            elif lower.endswith(".ogg") or lower.endswith(".opus"):
                content_type = "audio/ogg"
            elif lower.endswith(".mp3"):
                content_type = "audio/mpeg"
            else:
                content_type = "audio/wav"

        # ElevenLabs Scribe STT API endpoint
        url = f"{ELEVENLABS_BASE_URL}/speech-to-text"
        headers = {
            "xi-api-key": self.api_key
        }
        files = {
            "file": (filename, audio_bytes, content_type)
        }
        data = {
            "model_id": "scribe_v1"
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, headers=headers, files=files, data=data)
                if response.status_code == 200:
                    result = response.json()
                    return result.get("text", "")
                else:
                    logger.warning(f"ElevenLabs Scribe STT responded with {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"ElevenLabs STT exception: {e}")

        # Fallback to Groq Whisper if Groq key is present
        if settings.GROQ_API_KEY:
            try:
                whisper_url = "https://api.groq.com/openai/v1/audio/transcriptions"
                w_headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}"}
                w_files = {"file": (filename, audio_bytes, "audio/wav")}
                w_data = {"model": "whisper-large-v3"}
                async with httpx.AsyncClient(timeout=25.0) as client:
                    w_res = await client.post(whisper_url, headers=w_headers, files=w_files, data=w_data)
                    if w_res.status_code == 200:
                        return w_res.json().get("text", "")
            except Exception as e:
                logger.error(f"Groq Whisper fallback error: {e}")

        return None

voice_service = ElevenLabsVoiceService()
