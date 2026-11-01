"""AI layer. Google Gemini only; everything degrades gracefully to programmatic output without a key.

Rules enforced here:
- Gemini never receives a request without the real, sourced data attached.
- Gemini output is structured (JSON schema) and validated; free text is capped.
- Every call is counted in the DB against GEMINI_DAILY_CALL_LIMIT.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Type, TypeVar

from pydantic import BaseModel, Field

from ..db import Database

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)


# ---- schemas -----------------------------------------------------------------------
class NewsDigest(BaseModel):
    summary: str = Field(description="3-5 sentences in the requested language summarising ONLY the provided items")
    overall_tone: str = Field(description="positive | negative | neutral | mixed")
    key_risks: list[str] = Field(default_factory=list, description="max 4, each tied to a provided item")
    key_catalysts: list[str] = Field(default_factory=list, description="max 4, each tied to a provided item")


class StockNarrative(BaseModel):
    overview: str = Field(description="2-3 sentences: what the company does and where it stands")
    why_moved: str = Field(description="Explain the recent price move using ONLY the provided data; say 'unknown' when data is insufficient")
    bull_case: list[str] = Field(default_factory=list)
    bear_case: list[str] = Field(default_factory=list)
    verdict: str = Field(description="One paragraph: investment view, horizon, what would change the view. Never promise returns.")
    confidence: float = Field(ge=0, le=1)


class CompareResult(BaseModel):
    winner: str = Field(description="ticker with the better risk/reward, or 'none'")
    rationale: str
    table_notes: list[str] = Field(default_factory=list)


class QAAnswer(BaseModel):
    answer: str
    data_used: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


SYSTEM = """You are the research desk of a careful long-term investor who buys real shares (cash equities,
no CFDs, no leverage, no shorting) through XTB and invests only in Shariah-compliant companies.
Write in {language}. Use ONLY the data in the request: prices, ratios, news items and filings are
retrieved from real providers and labelled with their source. Never invent numbers, news, dates,
links or results. If something is missing, say it is missing. Separate clearly what is data,
what is calculation, and what is your interpretation. Never say 'guaranteed', 'risk-free' or
similar. Recommendations are research, not orders."""


class AIClient:
    """Base: does nothing. Replace with GeminiClient when a key is present."""
    name = "none"
    enabled = False

    def generate(self, task: str, payload: dict[str, Any], schema: Type[M], language: str = "ar") -> M | None:  # noqa: ARG002
        return None


class GeminiClient(AIClient):
    name = "Google Gemini"
    enabled = True

    def __init__(self, api_key: str, model: str, db: Database, daily_limit: int = 200) -> None:
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.db = db
        self.daily_limit = daily_limit

    def remaining_today(self) -> int:
        return max(0, self.daily_limit - self.db.usage_today("gemini"))

    def generate(self, task: str, payload: dict[str, Any], schema: Type[M], language: str = "ar") -> M | None:
        from google.genai import types

        if self.remaining_today() <= 0:
            log.warning("Gemini daily limit reached (%d); skipping %s", self.daily_limit, task)
            return None
        lang = {"ar": "Arabic (clear, Gulf-friendly, no slang)", "en": "English"}.get(language, language)
        prompt = f"TASK: {task}\n\nDATA (JSON, with sources):\n{json.dumps(payload, ensure_ascii=False, default=str)[:60000]}"
        ok = False
        try:
            resp = self.client.models.generate_content(
                model=self.model, contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM.format(language=lang),
                    response_mime_type="application/json", response_schema=schema, temperature=0.3,
                ),
            )
            parsed = getattr(resp, "parsed", None)
            if parsed is None and getattr(resp, "text", None):
                parsed = schema.model_validate_json(resp.text)
            ok = parsed is not None
            return parsed  # type: ignore[return-value]
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini %s failed: %s", task, exc)
            return None
        finally:
            self.db.usage_record("gemini", task, ok)


def build_ai(settings, db: Database) -> AIClient:  # noqa: ANN001
    if settings.gemini_api_key:
        try:
            return GeminiClient(settings.gemini_api_key, settings.gemini_model, db, settings.gemini_daily_call_limit)
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini unavailable: %s", exc)
    return AIClient()
