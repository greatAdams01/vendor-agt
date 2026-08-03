from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # App
    app_name: str = "ChopAgent"
    debug: bool = False

    # Database
    database_url: str = "postgresql+psycopg://chopagent:chopagent@localhost:5432/chopagent"

    # WhatsApp Cloud API
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v20.0"
    whatsapp_verify_token: str = ""  # used to verify webhook handshake

    # OpenAI (used by LangGraph agents)
    openai_api_key: str = ""

    # Paystack
    paystack_secret_key: str = ""
    paystack_base_url: str = "https://api.paystack.co"

    # Default vendor phone (E.164) for alerts when no dedicated alert target
    default_vendor_alert_phone: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()