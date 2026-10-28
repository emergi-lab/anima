from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """服务配置：环境变量统一以 PERSONA_ 开头，也可写在 .env。"""

    model_config = SettingsConfigDict(
        env_prefix="PERSONA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    # 桌宠只在本机运行，只监听回环地址。
    host: str = "127.0.0.1"
    port: int = Field(default=8030, ge=1, le=65535)
    # "token=namespace,token2=namespace2"; each token sees only its namespace.
    service_tokens: SecretStr = SecretStr("anima-local-token=anima")

    # 本地 SQLite 文件；测试用 sqlite+aiosqlite 内存库或临时文件覆盖。
    database_url: str = "sqlite+aiosqlite:///data/anima.db"
    orm_auto_create: bool = True

    # "fake" is an offline stand-in for tests and development only; with incomplete settings,
    # everything that needs the model answers 412 model_not_configured.
    model_backend: Literal["openai", "fake"] = "openai"
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = ""
    model: str = ""
    # DeepSeek V4 on TokenHub thinks by default; other OpenAI-compatible services may reject the
    # extra parameter, so the settings page can turn it off.
    disable_thinking: bool = True
    # The model service saved from the pet's settings page (overrides the four fields above).
    model_file: str = "data/model.json"
    # Model calls go straight to the service, ignoring the system's and the shell's proxy settings
    # (a local proxy added ~1.7 s before the first token). Set a proxy URL only if the service
    # cannot be reached without one, e.g. "http://127.0.0.1:7890".
    model_proxy: str = ""
    model_timeout_seconds: float = Field(default=90, ge=1, le=600)
    state_temperature: float = Field(default=0.2, ge=0, le=2)
    generation_temperature: float = Field(default=0.7, ge=0, le=2)
    # 单用户桌宠：对话、蒸馏与后台任务共享这几路并发。
    max_concurrent_model_calls: int = Field(default=4, ge=1, le=32)
    # 桌宠聊天：这么久没人说话就结束这段对话，整理成记忆。
    chat_idle_minutes: float = Field(default=10, gt=0, le=1440)
    max_personas: int = Field(default=5, ge=1, le=1000)  # per namespace (the pet's owner)

    def model_configured(self) -> bool:
        return all(value.strip() for value in (
            self.openai_base_url, self.model, self.openai_api_key.get_secret_value()))

    def token_namespaces(self) -> dict[str, str]:
        pairs = {}
        for item in self.service_tokens.get_secret_value().split(","):
            token, sep, namespace = item.strip().partition("=")
            if token and sep and namespace:
                pairs[token] = namespace
        return pairs
