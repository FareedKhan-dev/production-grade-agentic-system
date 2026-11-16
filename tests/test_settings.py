"""The JWT secret fails closed, and the files that start the app point at code that exists."""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values

from src.config.settings import (
    KNOWN_PLACEHOLDER_SECRETS,
    check_jwt_settings,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED_COMPOSE_DEFAULT = "supersecretkeythatshouldbechangedforproduction"


@pytest.mark.parametrize("secret", ["", *sorted(KNOWN_PLACEHOLDER_SECRETS), PUBLISHED_COMPOSE_DEFAULT.upper()])
def test_missing_and_published_secrets_are_refused(secret):
    assert check_jwt_settings(secret, "HS256")


def test_short_secrets_are_refused():
    assert "shorter than 32 bytes" in check_jwt_settings("k" * 31, "HS256")
    assert check_jwt_settings("k" * 48, "HS512")
    assert check_jwt_settings("k" * 64, "HS512") is None


def test_only_hmac_algorithms_are_accepted():
    for algorithm in ("none", "RS256", "ES256", ""):
        assert check_jwt_settings(secrets.token_urlsafe(64), algorithm)


def test_a_random_secret_is_accepted():
    assert check_jwt_settings(secrets.token_urlsafe(64), "HS256") is None


def test_the_api_refuses_to_start_with_the_published_compose_default():
    env = {**os.environ, "JWT_SECRET_KEY": PUBLISHED_COMPOSE_DEFAULT}
    result = subprocess.run(
        [sys.executable, "-c", "import src.main"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300
    )
    assert result.returncode != 0
    assert "Refusing to start: JWT_SECRET_KEY is a published example value" in result.stderr


def test_compose_has_no_fallback_secret():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert PUBLISHED_COMPOSE_DEFAULT not in text
    app = yaml.safe_load(text)["services"]["app"]
    assert not [entry for entry in app.get("environment", []) if entry.startswith("JWT_SECRET_KEY")]


def test_an_unedited_env_example_does_not_start_the_api():
    values = dotenv_values(ROOT / ".env.example")
    assert check_jwt_settings(values.get("JWT_SECRET_KEY") or "", values["JWT_ALGORITHM"])


def test_compose_does_not_hide_the_code_behind_a_mount():
    app = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["app"]
    for volume in app.get("volumes", []):
        assert not volume.split(":")[1].startswith("/app/src"), volume


def test_the_api_container_may_hold_many_connections():
    # Docker's default soft limit (1024 open files) breaks the API past ~1000 concurrent clients
    app = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["app"]
    assert app["ulimits"]["nofile"]["soft"] >= 65536


def test_every_launcher_starts_the_module_that_exists():
    assert (ROOT / "src" / "main.py").is_file()
    for name in ("Dockerfile", "Makefile", "scripts/set_env.sh"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "src.main:app" in text, name
        assert "app.main:app" not in text, name


def test_env_files_stay_out_of_git_and_out_of_the_image():
    for name in (".gitignore", ".dockerignore"):
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        assert ".env.*" in lines and "!.env.example" in lines, name
