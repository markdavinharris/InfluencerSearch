"""OpenAI-compatible client wrapper for chat (classification) and embeddings.

Provider-agnostic: base_url / api_key / model all come from settings, so switching
between Anthropic, OpenAI, OpenRouter, a local server, etc. is an env change. Every call is
logged to `llm_calls`, and chat calls refuse to run once today's token total reaches
LLM_DAILY_TOKEN_CAP.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import Engine, func, insert, select
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from app.config import Settings, get_settings
from app.db import get_engine
from app.log import get_logger
from app.models import llm_calls

log = get_logger(__name__)


class TokenCapExceeded(RuntimeError):
    pass


def _transient(exc: BaseException) -> bool:
    import openai

    if isinstance(exc, openai.APIConnectionError | openai.APITimeoutError):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


_retry = retry(
    retry=retry_if_exception(_transient),
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(multiplier=2, max=60),
    reraise=True,
)


@dataclass
class ChatResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


class ChatClient(Protocol):
    model: str

    def chat(self, system: str, user: str, purpose: str, max_tokens: int = 4096) -> ChatResult: ...


class EmbedClient(Protocol):
    model: str

    def embed(self, texts: list[str], purpose: str = "embed") -> list[list[float]]: ...


def tokens_used_today(engine: Engine) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with engine.connect() as conn:
        v = conn.execute(
            select(
                func.coalesce(func.sum(llm_calls.c.input_tokens + llm_calls.c.output_tokens), 0)
            ).where(llm_calls.c.created_at >= start)
        ).scalar_one()
    return int(v)


def log_call(
    engine: Engine, purpose: str, model: str, input_tokens: int, output_tokens: int
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(llm_calls).values(
                purpose=purpose, model=model, input_tokens=input_tokens, output_tokens=output_tokens
            )
        )


class LLM:
    """Chat-completions client for classification."""

    def __init__(
        self,
        settings: Settings | None = None,
        engine: Engine | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        from openai import OpenAI

        self.settings = settings or get_settings()
        self.engine = engine or get_engine()
        self.model = model or self.settings.llm_model
        self.client = OpenAI(
            base_url=base_url or self.settings.llm_base_url,
            api_key=api_key or self.settings.llm_api_key or "unset",
            max_retries=0,  # tenacity handles retries
            timeout=120,
        )

    def check_cap(self) -> None:
        used = tokens_used_today(self.engine)
        if used >= self.settings.llm_daily_token_cap:
            raise TokenCapExceeded(
                f"LLM daily token cap reached ({used} >= {self.settings.llm_daily_token_cap})"
            )

    @_retry
    def _create(self, **kwargs: Any) -> Any:
        return self.client.chat.completions.create(**kwargs)

    def chat(self, system: str, user: str, purpose: str, max_tokens: int = 4096) -> ChatResult:
        self.check_cap()
        resp = self._create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        usage = resp.usage
        result = ChatResult(
            text=resp.choices[0].message.content or "",
            model=resp.model or self.model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
        log_call(self.engine, purpose, self.model, result.input_tokens, result.output_tokens)
        return result


class Embedder:
    """/v1/embeddings client."""

    def __init__(self, settings: Settings | None = None, engine: Engine | None = None) -> None:
        from openai import OpenAI

        self.settings = settings or get_settings()
        self.engine = engine or get_engine()
        self.model = self.settings.embed_model
        self.client = OpenAI(
            base_url=self.settings.embed_base_url,
            api_key=self.settings.embed_api_key or "unset",
            max_retries=0,
            timeout=120,
        )

    @_retry
    def _create(self, **kwargs: Any) -> Any:
        return self.client.embeddings.create(**kwargs)

    def embed(self, texts: list[str], purpose: str = "embed") -> list[list[float]]:
        resp = self._create(model=self.model, input=texts)
        tokens = getattr(resp.usage, "prompt_tokens", 0) or 0
        log_call(self.engine, purpose, self.model, tokens, 0)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
