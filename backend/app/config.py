import os
from pathlib import Path
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load .env file from the backend folder
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Settings(BaseSettings):
    # App
    APP_NAME: str = "AgentTool Autonomous Mobile Assistant"
    DEBUG: bool = True
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # MongoDB Database (Local & Running on port 27017)
    MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "agent_tool_db")

    # Gemma LLM Provider (100% Local with Ollama - No Google API Key Needed)
    GEMMA_PROVIDER: str = os.getenv("GEMMA_PROVIDER", "ollama")
    GEMMA_MODEL_NAME: str = os.getenv("GEMMA_MODEL_NAME", "gemma:2b")
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # ElevenLabs Voice Settings
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
    ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

    # Security: token required for /api and /ws requests that don't come from this machine
    # (USB phones via `adb reverse` arrive as localhost). Auto-generated into .agenttool_token if unset.
    API_TOKEN: str = os.getenv("AGENTTOOL_API_TOKEN", "")
    # Extra browser origins allowed to call the API cross-origin (comma-separated). Same-origin always works.
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "")

    class Config:
        env_file = ".env"
        extra = "allow"

settings = Settings()

TOKEN_FILE = BASE_DIR / ".agenttool_token"
if not settings.API_TOKEN:
    # Persist the generated token so it survives restarts and uvicorn reload worker processes
    if TOKEN_FILE.exists():
        settings.API_TOKEN = TOKEN_FILE.read_text().strip()
    if not settings.API_TOKEN:
        import secrets
        settings.API_TOKEN = secrets.token_urlsafe(24)
        TOKEN_FILE.write_text(settings.API_TOKEN)
        TOKEN_FILE.chmod(0o600)
