"""Конфигурация сервиса. Все значения читаются из переменных окружения (.env)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from .schemas import PipelineMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- общее ---
    public_base_url: str = "http://localhost:8080"
    cors_origins: str = "http://localhost:8080"
    api_key: str = ""
    max_upload_mb: int = 1024
    retention_hours: int = 24
    data_dir: str = "/data"
    # Значение по умолчанию; действующий режим хранится в Redis и переключается через /admin
    pipeline_mode: PipelineMode = "direct"

    # --- ASR ---
    asr_base_url: str = "http://asr:8000/v1"
    asr_api_key: str = ""
    whisper__model: str = "Systran/faster-whisper-small"
    # initial_prompt по умолчанию, если в задаче не задан свой: термины, имена
    asr_prompt: str = ""
    # Расширения faster-whisper (Speaches). Облачный OpenAI их не знает — там выключите
    asr_vad_filter: bool = True
    asr_hotwords: str = ""
    # Длинная запись режется по паузам на куски ~N секунд (0 = не резать)
    asr_chunk_seconds: int = 600
    # Порог зацикливания: коэффициент сжатия окна текста (норма 2–2.6, петля 10+)
    asr_loop_threshold: float = 4.0
    # Вырезать типовые галлюцинации-«титры» («Редактор субтитров А.Семкин», «Продолжение следует…»)
    asr_hallucination_filter: bool = True
    asr_hallucination_file: str = ""  # свои регулярки, по одной на строку; дополняют встроенный список
    # Петля из короткой фразы (1–4 слова): повтор подряд ≥ N раз схлопывается до ASR_REPEAT_KEEP (0 = выкл.)
    asr_repeat_threshold: int = 4
    asr_repeat_keep: int = 1

    # --- LLM ---
    llm_base_url: str = "http://ollama:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "gemma3:12b"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 2048
    # Для «думающих» моделей (gemma4, qwen3…): none/low/medium/high. Пусто = параметр не передаётся
    llm_reasoning_effort: str = ""
    # Кусок текста на один запрос постобработки (символы; ~1 000 символов ≈ 1 мин речи)
    llm_chunk_chars: int = 12_000

    # --- нормализация (пунктуация + термины по глоссарию) ---
    normalize_default: bool = True
    # Крупная пачка = меньше запросов и больше контекста для исправления терминов;
    # 8 000 символов ≈ 3.6k токенов на вход и столько же на выход (нужен контекст ≥ 12k)
    normalize_chunk_chars: int = 8_000
    # Потолок токенов ответа на пачку; 0 = считать от размера пачки
    normalize_max_tokens: int = 0
    glossary_file: str = ""  # пусто = встроенный app/glossary.txt

    # --- диаризация (сервис diarizer, см. diarizer/) ---
    # Пустой URL = диаризация недоступна. Включена по умолчанию, если сервис задан
    diarize_base_url: str = "http://diarizer:8000"
    diarize_default: bool = True

    # --- доставка ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    outbound_webhook_url: str = ""
    outbound_webhook_token: str = ""

    # --- n8n ---
    n8n_webhook_url: str = "http://n8n:5678/webhook/transcriber"
    n8n_webhook_token: str = ""

    # --- инфраструктура ---
    redis_url: str = "redis://redis:6379/0"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
