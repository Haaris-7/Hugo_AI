"""Server-side configuration used by the web setup wizard."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import CAPABILITY_ENV, get_settings

ENV_PATH = Path(os.environ.get("ARGO_ENV_FILE", ".env"))


@dataclass(frozen=True)
class Field:
    key: str
    prompt: str
    default: str = ""
    secret: bool = False


WIZARD_FIELDS: list[Field] = [
    Field("ARGO_HERMES_BASE_URL", "NemoClaw Hermes base URL", "http://host.docker.internal:8642/v1"),
    Field("ARGO_DEMO_MODE", "Seed demo campaigns", "false"),
    Field("ARGO_DEMO_REAL_PROVIDERS", "Use real providers in demo", ""),
    Field("ARGO_HERMES_API_KEY", "Hermes API key", secret=True),
    Field("ARGO_NVIDIA_API_KEY", "NVIDIA NIM API key", secret=True),
    Field("ARGO_NVIDIA_VISION_MODEL", "NIM vision model", "nvidia/nemotron-nano-12b-v2-vl"),
    Field("ARGO_STRIPE_SECRET_KEY", "Stripe secret key", secret=True),
    Field("ARGO_STRIPE_WEBHOOK_SECRET", "Stripe webhook secret", secret=True),
    Field("ARGO_GMAIL_ACCESS_TOKEN", "Gmail OAuth access token", secret=True),
    Field("ARGO_GMAIL_CLIENT_ID", "Google OAuth client ID", secret=True),
    Field("ARGO_GMAIL_CLIENT_SECRET", "Google OAuth client secret", secret=True),
    Field("ARGO_GMAIL_REFRESH_TOKEN", "Google OAuth refresh token", secret=True),
    Field("ARGO_GMAIL_SENDER", "Gmail sender address"),
    Field("ARGO_DISCOVERY_MODE", "Creator discovery method", "hermes_agents"),
    Field("ARGO_INFLUENCERS_CLUB_API_KEY", "Influencers.club API key", secret=True),
    Field("ARGO_YOUTUBE_API_KEY", "YouTube Data API key", secret=True),
    Field("ARGO_TELEGRAM_BOT_TOKEN", "Telegram bot token", secret=True),
]

SECRET_KEYS = {field.key for field in WIZARD_FIELDS if field.secret}


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_env(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    invalid = [key for key, value in updates.items() if any(char in value for char in "\r\n\0")]
    if invalid:
        raise ValueError(f"Environment values cannot contain newlines: {', '.join(invalid)}")
    current = read_env(path)
    current.update({key: value for key, value in updates.items() if value is not None})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("\n".join(f"{key}={value}" for key, value in current.items()) + "\n")
    temporary.chmod(0o600)
    try:
        temporary.replace(path)
    except OSError:
        # Bind-mounted files can't be atomically replaced; write in-place
        path.write_text(temporary.read_text())
        temporary.unlink(missing_ok=True)
    path.chmod(0o600)


def mask(key: str, value: str) -> str:
    if not value:
        return ""
    if key in SECRET_KEYS:
        return value[:3] + "…" + value[-2:] if len(value) > 6 else "set"
    return value


def summary() -> dict:
    get_settings.cache_clear()
    settings = get_settings()
    env = read_env()
    try:
        settings.validate_runtime()
        validation = {"ok": True, "problems": []}
    except RuntimeError as exc:
        validation = {"ok": False, "problems": str(exc).split(": ", 1)[-1].split("; ")}
    return {
        "config": {field.key: mask(field.key, env.get(field.key, "")) for field in WIZARD_FIELDS},
        "capabilities": settings.capability_modes(),
        "required_env": CAPABILITY_ENV,
        "validation": validation,
        "demo_mode": settings.demo_mode,
    }


def apply_updates(updates: dict[str, str]) -> dict:
    allowed = {field.key for field in WIZARD_FIELDS}
    filtered = {key: value for key, value in updates.items() if key in allowed}
    previous = read_env()
    prev_demo = previous.get("ARGO_DEMO_MODE", "false").lower() in ("true", "1", "yes")
    write_env(filtered)
    get_settings.cache_clear()
    new_demo = read_env().get("ARGO_DEMO_MODE", "false").lower() in ("true", "1", "yes")
    if new_demo != prev_demo:
        from .db import SessionLocal
        from .demo import clear_demo_data, seed_demo_data

        with SessionLocal() as db:
            if new_demo:
                seed_demo_data(db)
            else:
                clear_demo_data(db)
    return summary()


def main() -> None:
    import json

    print(json.dumps(summary(), indent=2))


if __name__ == "__main__":
    main()
