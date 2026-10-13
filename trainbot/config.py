from dataclasses import dataclass, field
from pathlib import Path
import os
import math

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    discord_token: str = field(repr=False)
    challenge_key: str = field(repr=False)
    standard_key: str = field(repr=False)
    challenge_url: str
    standard_url: str
    public_url: str
    ai_enabled: bool
    ai_timeout: int
    ai_max_pending: int
    ai_max_sessions: int
    ai_ttl: int
    ai_max_turns: int
    ai_max_input: int
    ai_temperature: float = 0.7
    ai_top_p: float = 0.9
    ai_max_response_tokens: int = 0

    @classmethod
    def load(cls):
        load_dotenv(os.environ.get("TRAINBOT_ENV_FILE", ROOT / ".env"), override=False)
        def positive(name, default):
            value = int(os.environ.get(name, default))
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            return value
        def bounded_float(name, default, lower, upper):
            value = float(os.environ.get(name, default))
            if not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{name} must be between {lower} and {upper}")
            return value
        response_tokens = int(os.getenv("AI_MAX_RESPONSE_TOKENS", "0"))
        if response_tokens < 0:
            raise ValueError("AI_MAX_RESPONSE_TOKENS must be non-negative")
        return cls(
            os.getenv("DISCORD_BOT_TOKEN", ""),
            os.getenv("ODPT_CHALLENGE_CONSUMER_KEY", ""),
            os.getenv("ODPT_STANDARD_CONSUMER_KEY", ""),
            os.getenv("ODPT_CHALLENGE_BASE_URL", "https://api-challenge.odpt.org/api/v4").rstrip("/"),
            os.getenv("ODPT_STANDARD_BASE_URL", "https://api.odpt.org/api/v4").rstrip("/"),
            os.getenv("ODPT_PUBLIC_BASE_URL", "https://api-public.odpt.org/api/v4").rstrip("/"),
            os.getenv("AI_ENABLED", "true").lower() in {"1", "true", "yes"},
            positive("AI_TIMEOUT_SECONDS", 90), positive("AI_MAX_PENDING", 4),
            positive("AI_MAX_SESSIONS", 32), positive("AI_SESSION_TTL_SECONDS", 1800),
            positive("AI_MAX_TURNS", 8), positive("AI_MAX_INPUT_CHARS", 2000),
            bounded_float("AI_TEMPERATURE", 0.7, 0.0, 1.0),
            bounded_float("AI_TOP_P", 0.9, 0.01, 1.0), response_tokens,
        )
