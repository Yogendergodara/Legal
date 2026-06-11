"""Legal AI Platform — orchestration layer for multi-agent legal AI."""

# Load .env into the process environment as early as possible. Several
# downstream modules (the Research Agent's model_config) construct LLM clients
# at import time and read credentials / LLM_BASE_URL from os.environ, so this
# must run before any submodule import.
from pathlib import Path

from dotenv import load_dotenv as _load_dotenv

# Always load legal_ai_platform/.env regardless of the process working directory.
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
_load_dotenv(_env_path)

__version__ = "0.1.0"
