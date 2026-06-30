from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

CAPABILITY_KEYS: dict[str, tuple[str, ...]] = {
    "hermes": ("hermes_api_key",),
    "vision": ("nvidia_api_key",),
    "stripe": ("stripe_secret_key", "stripe_webhook_secret"),
    "gmail": ("gmail_sender",),
    "youtube": ("youtube_api_key",),
    "telegram": ("telegram_bot_token",),
    "discovery": ("influencers_club_api_key",),
}

CAPABILITY_ENV: dict[str, str] = {
    "hermes": "ARGO_HERMES_API_KEY",
    "vision": "ARGO_NVIDIA_API_KEY",
    "stripe": "ARGO_STRIPE_SECRET_KEY and ARGO_STRIPE_WEBHOOK_SECRET",
    "gmail": "ARGO_GMAIL_ACCESS_TOKEN and ARGO_GMAIL_SENDER",
    "youtube": "ARGO_YOUTUBE_API_KEY",
    "telegram": "ARGO_TELEGRAM_BOT_TOKEN",
    "discovery": "ARGO_INFLUENCERS_CLUB_API_KEY",
}

REQUIRED_CAPABILITIES = ("hermes", "vision", "stripe", "gmail")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="ARGO_", case_sensitive=False, extra="ignore"
    )

    env: str = "development"
    database_url: str = "sqlite:///./argo.db"
    api_token: str = "change-me"
    agent_token: str = "change-agent-token"
    inline_jobs: bool = True
    automation_poll_seconds: int = 30
    gmail_lookback_days: int = 30
    discovery_refill_cents: int = 100
    discovery_mode: Literal["influencers_club", "hermes_agents"] = "hermes_agents"
    influencers_club_api_key: str = ""

    demo_mode: bool = False

    hermes_base_url: str = "http://host.docker.internal:8642/v1"
    hermes_api_key: str = ""
    hermes_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    require_nemoclaw: bool = True
    hermes_cron_active: bool = False

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_success_url: str = "http://localhost:3000/campaigns/{campaign_id}?funding=success"
    stripe_cancel_url: str = "http://localhost:3000/campaigns/{campaign_id}?funding=cancelled"
    stripe_connect_refresh_url: str = "http://localhost:3000/system"
    stripe_connect_return_url: str = "http://localhost:3000/system"

    gmail_access_token: str = ""
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_sender: str = ""
    nvidia_api_key: str = ""
    nvidia_vision_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_vision_model: str = "nvidia/nemotron-nano-12b-v2-vl"
    youtube_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_approval_mode: Literal[
        "strategy_creators", "strategy_creators_payments", "full_autonomy"
    ] = "full_autonomy"

    def capability_configured(self, capability: str) -> bool:
        if capability == "gmail":
            renewable = all(
                (self.gmail_client_id, self.gmail_client_secret, self.gmail_refresh_token)
            )
            return bool(self.gmail_sender and (self.gmail_access_token or renewable))
        keys = CAPABILITY_KEYS.get(capability, ())
        return bool(keys) and all(bool(getattr(self, key)) for key in keys)

    def should_attempt_live(self, capability: str) -> bool:
        """Compatibility helper for providers; production has no simulated mode."""
        return self.capability_configured(capability)

    def capability_modes(self) -> dict[str, dict[str, object]]:
        states: dict[str, dict[str, object]] = {}
        for capability in CAPABILITY_KEYS:
            if capability == "discovery":
                if self.discovery_mode == "influencers_club":
                    configured = self.capability_configured("discovery")
                    states["discovery"] = {
                        "resolved": "ready" if configured else "missing",
                        "credentials_present": configured,
                        "required": True,
                        "mode": "influencers_club",
                    }
                else:
                    states["discovery"] = {
                        "resolved": "agent_managed",
                        "credentials_present": True,
                        "required": True,
                        "mode": "hermes_agents",
                        "managed_by": "Hermes",
                    }
                continue
            states[capability] = {
                "resolved": "ready" if self.capability_configured(capability) else "missing",
                "credentials_present": self.capability_configured(capability),
                "required": capability in REQUIRED_CAPABILITIES,
            }
        return states

    def validate_runtime(self) -> None:
        if self.demo_mode:
            return
        problems: list[str] = []
        if self.api_token in ("", "change-me"):
            problems.append("ARGO_API_TOKEN must be set to a non-default value")
        if self.agent_token in ("", "change-agent-token"):
            problems.append("ARGO_AGENT_TOKEN must be set to a non-default value")
        for capability in REQUIRED_CAPABILITIES:
            if not self.capability_configured(capability):
                problems.append(f"{CAPABILITY_ENV[capability]} is required")
        if not self.require_nemoclaw:
            problems.append("ARGO_REQUIRE_NEMOCLAW must remain true")
        if self.hermes_model != "nvidia/nemotron-3-ultra-550b-a55b":
            problems.append("Hermes must use nvidia/nemotron-3-ultra-550b-a55b")
        if problems:
            raise RuntimeError("Configuration problems: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
