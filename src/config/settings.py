"""Application configuration management.

This module handles environment-specific configuration loading, parsing, and management
for the application. It includes environment detection, .env file loading, and
configuration value parsing.
"""

import json
import os
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Union,
)

from dotenv import load_dotenv


# Define environment types
class Environment(str, Enum):
    """Application environment types.

    Defines the possible environments the application can run in:
    development, staging, production, and test.
    """

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


# Determine environment
def get_environment() -> Environment:
    """Get the current environment.

    Returns:
        Environment: The current environment (development, staging, production, or test)
    """
    match os.getenv("APP_ENV", "development").lower():
        case "production" | "prod":
            return Environment.PRODUCTION
        case "staging" | "stage":
            return Environment.STAGING
        case "test":
            return Environment.TEST
        case _:
            return Environment.DEVELOPMENT


# Load appropriate .env file based on environment
def load_env_file():
    """Load environment-specific .env file."""
    env = get_environment()
    print(f"Loading environment: {env}")
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    # Define env files in priority order
    env_files = [
        os.path.join(base_dir, f".env.{env.value}.local"),
        os.path.join(base_dir, f".env.{env.value}"),
        os.path.join(base_dir, ".env.local"),
        os.path.join(base_dir, ".env"),
    ]

    # Load the first env file that exists
    for env_file in env_files:
        if os.path.isfile(env_file):
            load_dotenv(dotenv_path=env_file)
            print(f"Loaded environment from {env_file}")
            return env_file

    # Fall back to default if no env file found
    return None


ENV_FILE = load_env_file()


# Parse list values from environment variables
def parse_list_from_env(env_key, default=None):
    """Parse a comma-separated list from an environment variable."""
    value = os.getenv(env_key)
    if not value:
        return default or []

    # Remove quotes if they exist
    value = value.strip("\"'")
    # Handle single value case
    if "," not in value:
        return [value]
    # Split comma-separated values
    return [item.strip() for item in value.split(",") if item.strip()]


# Values published as examples (in this repository, its README or the article). Anyone can
# read them, so a token signed with one of them can be forged by anyone.
KNOWN_PLACEHOLDER_SECRETS = frozenset(
    {
        "supersecretkeythatshouldbechangedforproduction",
        "your-jwt-secret-key",
        "unsafe-secret-for-dev",
        "changeme",
        "change-me",
        "secret",
    }
)

# RFC 7518, section 3.2: an HMAC key must be at least as long as the hash output.
JWT_MIN_SECRET_BYTES = {"HS256": 32, "HS384": 48, "HS512": 64}

JWT_SECRET_HINT = 'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'


def check_jwt_settings(secret: str, algorithm: str) -> Optional[str]:
    """Say why the JWT settings are unsafe, or return None when they are fine.

    Every token is an HMAC of its claims, so whoever knows JWT_SECRET_KEY can sign a token
    for any user. The key must be secret (not a value published as an example) and long
    enough that it cannot be guessed.

    Args:
        secret: The value of JWT_SECRET_KEY.
        algorithm: The value of JWT_ALGORITHM.

    Returns:
        Optional[str]: The problem, or None.
    """
    if algorithm not in JWT_MIN_SECRET_BYTES:
        return f"JWT_ALGORITHM must be one of {', '.join(JWT_MIN_SECRET_BYTES)}, not {algorithm!r}"
    if not secret:
        return "JWT_SECRET_KEY is not set"
    if secret.strip().lower() in KNOWN_PLACEHOLDER_SECRETS:
        return "JWT_SECRET_KEY is a published example value, so anyone could sign tokens with it"
    minimum = JWT_MIN_SECRET_BYTES[algorithm]
    if len(secret.encode("utf-8")) < minimum:
        return f"JWT_SECRET_KEY is shorter than {minimum} bytes, the minimum for {algorithm}"
    return None


# Parse dict of lists from environment variables with prefix
def parse_dict_of_lists_from_env(prefix, default_dict=None):
    """Parse dictionary of lists from environment variables with a common prefix."""
    result = default_dict or {}

    # Look for all env vars with the given prefix
    for key, value in os.environ.items():
        if key.startswith(prefix):
            endpoint = key[len(prefix) :].lower()  # Extract endpoint name
            # Parse the values for this endpoint
            if value:
                value = value.strip("\"'")
                if "," in value:
                    result[endpoint] = [item.strip() for item in value.split(",") if item.strip()]
                else:
                    result[endpoint] = [value]

    return result


class Settings:
    """Application settings without using pydantic."""

    def __init__(self):
        """Initialize application settings from environment variables.

        Loads and sets all configuration values from environment variables,
        with appropriate defaults for each setting. Also applies
        environment-specific overrides based on the current environment.
        """
        # Set the environment
        self.ENVIRONMENT = get_environment()

        # Application Settings
        self.PROJECT_NAME = os.getenv("PROJECT_NAME", "FastAPI LangGraph Template")
        self.VERSION = os.getenv("VERSION", "1.0.0")
        self.DESCRIPTION = os.getenv(
            "DESCRIPTION", "A production-ready FastAPI template with LangGraph and Langfuse integration"
        )
        self.API_V1_STR = os.getenv("API_V1_STR", "/api/v1")
        self.DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "t", "yes")

        # CORS Settings
        self.ALLOWED_ORIGINS = parse_list_from_env("ALLOWED_ORIGINS", ["*"])

        # Langfuse Configuration
        self.LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        self.LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
        self.LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

        # LangGraph Configuration
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
        # Any OpenAI-compatible server (Azure OpenAI, vLLM, Ollama, OpenRouter...). Always passed
        # explicitly: the OpenAI client would otherwise read the variable itself, and an empty one
        # would leave it without a URL.
        self.OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        self.DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "gpt-5-mini")
        # Models to try, in order, when the current one keeps failing. Unset: the built-in OpenAI
        # models. Set but empty: no fallback.
        fallback_models = os.getenv("LLM_FALLBACK_MODELS")
        self.LLM_FALLBACK_MODELS = (
            None if fallback_models is None else [m.strip() for m in fallback_models.split(",") if m.strip()]
        )
        self.DEFAULT_LLM_TEMPERATURE = float(os.getenv("DEFAULT_LLM_TEMPERATURE", "0.2"))
        # Most tokens the model may write in one answer
        self.MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2000"))
        # Most tokens of conversation history sent with each request (the oldest messages go first)
        self.MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "16000"))
        self.MAX_LLM_CALL_RETRIES = int(os.getenv("MAX_LLM_CALL_RETRIES", "3"))

        # Long term memory Configuration
        self.LONG_TERM_MEMORY_MODEL = os.getenv("LONG_TERM_MEMORY_MODEL", "gpt-5-nano")
        self.LONG_TERM_MEMORY_EMBEDDER_MODEL = os.getenv("LONG_TERM_MEMORY_EMBEDDER_MODEL", "text-embedding-3-small")
        # Must match the embedder: 1536 for text-embedding-3-small, 768 for nomic-embed-text...
        self.LONG_TERM_MEMORY_EMBEDDER_DIMS = int(os.getenv("LONG_TERM_MEMORY_EMBEDDER_DIMS", "1536"))
        self.LONG_TERM_MEMORY_COLLECTION_NAME = os.getenv("LONG_TERM_MEMORY_COLLECTION_NAME", "longterm_memory")
        # JWT Configuration. There is deliberately no default key: the API refuses to start
        # until JWT_SECRET_KEY is set to a long random value (see check_jwt_settings).
        self.JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
        self.JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
        self.JWT_ACCESS_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_DAYS", "7"))

        # Logging Configuration
        self.LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
        self.LOG_FORMAT = os.getenv("LOG_FORMAT", "json")  # "json" or "console"

        # Postgres Configuration
        self.POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
        self.POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
        self.POSTGRES_DB = os.getenv("POSTGRES_DB", "food_order_db")
        self.POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
        self.POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
        self.POSTGRES_POOL_SIZE = int(os.getenv("POSTGRES_POOL_SIZE", "20"))
        # Threads for blocking work run from async code: database queries and mem0's model and
        # embedding calls. asyncio's default (CPU count + 4) is sized for CPU-bound work.
        self.THREAD_POOL_SIZE = int(os.getenv("THREAD_POOL_SIZE", "64"))
        self.POSTGRES_MAX_OVERFLOW = int(os.getenv("POSTGRES_MAX_OVERFLOW", "10"))
        self.CHECKPOINT_TABLES = ["checkpoint_blobs", "checkpoint_writes", "checkpoints"]

        # Rate Limiting Configuration
        self.RATE_LIMIT_DEFAULT = parse_list_from_env("RATE_LIMIT_DEFAULT", ["200 per day", "50 per hour"])

        # Rate limit endpoints defaults
        default_endpoints = {
            "chat": ["30 per minute"],
            "chat_stream": ["20 per minute"],
            "messages": ["50 per minute"],
            "register": ["10 per hour"],
            "login": ["20 per minute"],
            "root": ["10 per minute"],
            "health": ["20 per minute"],
        }

        # Update rate limit endpoints from environment variables
        self.RATE_LIMIT_ENDPOINTS = default_endpoints.copy()
        for endpoint in default_endpoints:
            env_key = f"RATE_LIMIT_{endpoint.upper()}"
            value = parse_list_from_env(env_key)
            if value:
                self.RATE_LIMIT_ENDPOINTS[endpoint] = value

        # Evaluation Configuration
        self.EVALUATION_LLM = os.getenv("EVALUATION_LLM", "gpt-5")
        self.EVALUATION_BASE_URL = os.getenv("EVALUATION_BASE_URL", "https://api.openai.com/v1")
        self.EVALUATION_API_KEY = os.getenv("EVALUATION_API_KEY", self.OPENAI_API_KEY)
        self.EVALUATION_SLEEP_TIME = int(os.getenv("EVALUATION_SLEEP_TIME", "10"))

        # Apply environment-specific settings
        self.apply_environment_settings()

    def apply_environment_settings(self):
        """Apply environment-specific settings based on the current environment."""
        env_settings = {
            Environment.DEVELOPMENT: {
                "DEBUG": True,
                "LOG_LEVEL": "DEBUG",
                "LOG_FORMAT": "console",
                "RATE_LIMIT_DEFAULT": ["1000 per day", "200 per hour"],
            },
            Environment.STAGING: {
                "DEBUG": False,
                "LOG_LEVEL": "INFO",
                "RATE_LIMIT_DEFAULT": ["500 per day", "100 per hour"],
            },
            Environment.PRODUCTION: {
                "DEBUG": False,
                "LOG_LEVEL": "WARNING",
                "RATE_LIMIT_DEFAULT": ["200 per day", "50 per hour"],
            },
            Environment.TEST: {
                "DEBUG": True,
                "LOG_LEVEL": "DEBUG",
                "LOG_FORMAT": "console",
                "RATE_LIMIT_DEFAULT": ["1000 per day", "1000 per hour"],  # Relaxed for testing
            },
        }

        # Get settings for current environment
        current_env_settings = env_settings.get(self.ENVIRONMENT, {})

        # Apply settings if not explicitly set in environment variables
        for key, value in current_env_settings.items():
            env_var_name = key.upper()
            # Only override if environment variable wasn't explicitly set
            if env_var_name not in os.environ:
                setattr(self, key, value)


# Create settings instance
settings = Settings()
