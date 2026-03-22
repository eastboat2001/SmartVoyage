import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE, override=False, encoding="utf-8")


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value
    return default


class Config:
    _env_notice_shown = False
    _api_key_notice_shown = False

    def __init__(self):
        self.project_root = str(PROJECT_ROOT)
        self.env_file = str(ENV_FILE)

        self.provider = _first_env(
            "SMARTVOYAGE_PROVIDER",
            default="openai_compatible",
        ).strip().lower()

        # 大模型配置
        self.base_url = _first_env(
            "SMARTVOYAGE_BASE_URL",
            "OPENAI_BASE_URL",
            default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.api_key = _first_env(
            "SMARTVOYAGE_API_KEY",
            "OPENAI_API_KEY",
            "DASHSCOPE_API_KEY",
            default="",
        )
        self.model_name = _first_env(
            "SMARTVOYAGE_MODEL_NAME",
            "OPENAI_MODEL_NAME",
            default="qwen-plus",
        )
        self.ollama_base_url = _first_env(
            "SMARTVOYAGE_OLLAMA_BASE_URL",
            "OLLAMA_BASE_URL",
            default="http://127.0.0.1:11434",
        )
        self.fallback_provider = _first_env(
            "SMARTVOYAGE_FALLBACK_PROVIDER",
            default="",
        ).strip().lower()
        self.fallback_model_name = _first_env(
            "SMARTVOYAGE_FALLBACK_MODEL_NAME",
            default="",
        )
        self.fallback_base_url = _first_env(
            "SMARTVOYAGE_FALLBACK_BASE_URL",
            default=self.base_url,
        )
        self.fallback_api_key = _first_env(
            "SMARTVOYAGE_FALLBACK_API_KEY",
            default=self.api_key,
        )
        self.fallback_ollama_base_url = _first_env(
            "SMARTVOYAGE_FALLBACK_OLLAMA_BASE_URL",
            default=self.ollama_base_url,
        )

        # 容错策略
        self.agent_timeout_seconds = float(
            _first_env("SMARTVOYAGE_AGENT_TIMEOUT_SECONDS", default="18")
        )
        self.structured_retry_count = max(
            1,
            int(_first_env("SMARTVOYAGE_STRUCTURED_RETRY_COUNT", default="2")),
        )
        self.text_retry_count = max(
            1,
            int(_first_env("SMARTVOYAGE_TEXT_RETRY_COUNT", default="2")),
        )

        # 数据库配置
        self.host = _first_env("SMARTVOYAGE_DB_HOST", "MYSQL_HOST", default="localhost")
        self.user = _first_env("SMARTVOYAGE_DB_USER", "MYSQL_USER", default="root")
        self.password = _first_env("SMARTVOYAGE_DB_PASSWORD", "MYSQL_PASSWORD", default="123456")
        self.database = _first_env("SMARTVOYAGE_DB_NAME", "MYSQL_DATABASE", default="travel_rag")
        self.default_username = _first_env("SMARTVOYAGE_DEFAULT_USERNAME", default="demo_user")
        self.default_user_phone = _first_env("SMARTVOYAGE_DEFAULT_USER_PHONE", default="13800000000")

        # 时间与工作流持久化配置
        self.timezone_name = _first_env(
            "SMARTVOYAGE_TIMEZONE",
            default="Asia/Shanghai",
        )
        self.now_override = _first_env(
            "SMARTVOYAGE_NOW_OVERRIDE",
            default="",
        )
        self.order_checkpoint_path = _first_env(
            "SMARTVOYAGE_ORDER_CHECKPOINT_PATH",
            default=str(PROJECT_ROOT / "data" / "checkpoints" / "transport_order.pkl"),
        )

        # 日志配置
        self.log_file = _first_env(
            "SMARTVOYAGE_LOG_FILE",
            default=str(PROJECT_ROOT / "logs" / "app.log"),
        )

        self._warn_if_needed()

    def _warn_if_needed(self) -> None:
        if not ENV_FILE.exists() and not Config._env_notice_shown:
            print(
                "[Config] 未找到 .env 文件，当前使用 config.py 默认值和系统环境变量。"
                "建议复制 .env.example 为 .env 后再填入实际配置。"
            )
            Config._env_notice_shown = True

        if self.provider not in {"openai_compatible", "ollama"}:
            print(
                f"[Config] 不支持的 provider: {self.provider}。"
                "当前仅支持 openai_compatible 或 ollama。"
            )

        if self.fallback_provider and self.fallback_provider not in {"openai_compatible", "ollama"}:
            print(
                f"[Config] 不支持的 fallback provider: {self.fallback_provider}。"
                "当前仅支持 openai_compatible 或 ollama。"
            )

        if self.provider == "openai_compatible" and not self.api_key and not Config._api_key_notice_shown:
            print(
                "[Config] provider=openai_compatible，但未检测到 API Key。"
                "请在 .env 中配置 SMARTVOYAGE_API_KEY 或 OPENAI_API_KEY，否则调用模型接口时会鉴权失败。"
            )
            Config._api_key_notice_shown = True


if __name__ == "__main__":
    conf = Config()
    print(f"log_file={conf.log_file}")
    print(f"env_file={conf.env_file}")
