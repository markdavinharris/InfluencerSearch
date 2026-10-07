"""Test doubles for the LLM and embedding clients."""

import json
import re
from collections.abc import Callable

from app.llm import ChatResult


class FakeLLM:
    """Classifies by a keyword rule; can be told to return garbage for the first N calls."""

    model = "fake-llm"

    def __init__(
        self,
        rule: Callable[[str], bool] | None = None,
        garbage_calls: int = 0,
        drop_ids: set[int] | None = None,
    ) -> None:
        self.rule = rule or (lambda t: "pjm" in t.lower() or "rggi" in t.lower())
        self.garbage_calls = garbage_calls
        self.drop_ids = drop_ids or set()
        self.calls: list[list[int]] = []

    def chat(self, system: str, user: str, purpose: str, max_tokens: int = 4096) -> ChatResult:
        items = json.loads(user.split("\n", 1)[1])
        self.calls.append([i["post_id"] for i in items])
        if self.garbage_calls > 0:
            self.garbage_calls -= 1
            return ChatResult("Sure! Here are the results: [{oops", self.model, 10, 10)
        out = []
        for i in items:
            if i["post_id"] in self.drop_ids:
                continue
            on = self.rule(i["text"])
            out.append(
                {
                    "post_id": i["post_id"],
                    "on_topic": on,
                    "confidence": 0.9 if on else 0.8,
                    "stance": "oppose" if "bad" in i["text"].lower() else "neutral",
                    "summary": "about PA energy" if on else "",
                }
            )
        return ChatResult("```json\n" + json.dumps(out) + "\n```", self.model, 100, 50)


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self, dims: int = 1536) -> None:
        self.dims = dims

    def embed(self, texts: list[str], purpose: str = "embed") -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dims
            for w in re.findall(r"\w+", t.lower()):
                v[hash(w) % self.dims] += 1.0
            out.append(v)
        return out
