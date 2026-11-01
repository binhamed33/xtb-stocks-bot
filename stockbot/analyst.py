"""Daily brief models + Gemini-backed analyst for the scheduled pre-market/close posts.

Gemini (google-genai) is the only AI provider. Without GEMINI_API_KEY the
scheduler uses `rules.RulesAnalyst` (programmatic picks) instead.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from .config import Settings
from .data import StockSnapshot

log = logging.getLogger(__name__)


class Pick(BaseModel):
    ticker: str
    action: Literal["BUY", "SELL", "AVOID", "HOLD"]
    conviction: float = Field(ge=0, le=1, description="Calibrated confidence 0-1")
    entry_zone: str = Field(default="", description="e.g. '182-185' or 'market'")
    stop_loss: float | None = Field(default=None, description="Absolute price; required for BUY")
    target: float | None = Field(default=None, description="Absolute price; required for BUY")
    horizon: str = Field(default="", description="e.g. '1-2 weeks', 'swing', 'position'")
    reason: str = Field(description="Two or three sentences, concrete, in the requested language")
    risk_note: str = Field(default="", description="What invalidates the idea")


class DailyBrief(BaseModel):
    market_mood: str = Field(description="One paragraph: overall market read for today")
    key_events: list[str] = Field(description="Important scheduled events / earnings / macro today, max 6")
    buys: list[Pick]
    sells: list[Pick] = Field(description="Positions to exit or take profit on; may be empty")
    avoid: list[Pick] = Field(description="Names to stay away from today, with why")
    top_idea: Pick | None = Field(default=None, description="The single best risk/reward idea today")
    holdings_advice: list[Pick] = Field(default_factory=list, description="One entry per user holding: HOLD or SELL")


class CloseSummary(BaseModel):
    summary: str = Field(description="One paragraph on how the session went")
    lessons: list[str] = Field(description="Up to 4 short takeaways", max_length=6)
    tomorrow_watch: list[str] = Field(description="Up to 5 things to watch tomorrow", max_length=6)


BRIEF_RULES = """Rules for the pre-market brief:
- Be selective. A BUY needs several aligned signals and a clear invalidation. Fewer, better picks.
- Every BUY includes an entry zone, an absolute stop_loss below a real level, and a target with at least
  2:1 reward-to-risk. Stops wider than ~8% are usually wrong for stocks.
- AVOID names with earnings in the next 2 trading days unless the thesis is about it; flag earnings within 7 days.
- SELL applies only to the user's holdings or earlier ideas: take profit at target or when the thesis broke.
- conviction is your honest probability the idea works within the horizon; most ideas are 0.5-0.65.
- Only tickers present in the input. Stay within the max counts. Prices with two decimals. No invented news."""


class Analyst:
    """Gemini-backed brief. `ai` is an AIClient from stockbot.ai (must be enabled)."""

    def __init__(self, settings: Settings, ai) -> None:  # noqa: ANN001
        self.s = settings
        self.ai = ai

    def daily_brief(self, snaps: list[StockSnapshot], indices: dict, today: str,
                    previous_picks: list[dict] | None = None) -> DailyBrief:
        payload = {
            "date": today, "rules": BRIEF_RULES,
            "limits": {"max_buys": self.s.max_buy_picks, "max_sells": self.s.max_sell_picks, "max_avoid": self.s.max_avoid_picks},
            "portfolio": {"size_usd": self.s.portfolio_size, "risk_per_trade_pct": self.s.risk_per_trade_pct},
            "holdings": [h.__dict__ for h in self.s.holdings],
            "previous_open_ideas": previous_picks or [],
            "indices": indices,
            "stocks": [s.as_prompt_dict() for s in snaps],
        }
        brief = self.ai.generate("Produce today's pre-market brief.", payload, DailyBrief, self.s.language)
        if brief is None:
            raise RuntimeError("Gemini returned no brief")
        valid = {s.ticker for s in snaps} | {h.ticker for h in self.s.holdings}
        brief.buys = [p for p in brief.buys if p.ticker.upper() in valid and p.stop_loss and p.target][: self.s.max_buy_picks]
        brief.sells = [p for p in brief.sells if p.ticker.upper() in valid][: self.s.max_sell_picks]
        brief.avoid = [p for p in brief.avoid if p.ticker.upper() in valid][: self.s.max_avoid_picks]
        for p in brief.buys + brief.sells + brief.avoid + brief.holdings_advice:
            p.ticker = p.ticker.upper()
        return brief

    def close_summary(self, indices: dict, picks_perf: list[dict], movers: list[dict], today: str) -> CloseSummary:
        payload = {"date": today, "indices": indices, "todays_ideas_performance": picks_perf, "biggest_movers": movers}
        res = self.ai.generate("Write the closing-bell summary from the data only.", payload, CloseSummary, self.s.language)
        if res is None:
            raise RuntimeError("Gemini returned no summary")
        return res


def build_analyst(settings: Settings, ai):  # noqa: ANN001
    """Gemini when available, programmatic rules otherwise."""
    if ai is not None and getattr(ai, "enabled", False):
        return Analyst(settings, ai)
    from .rules import RulesAnalyst

    log.warning("GEMINI_API_KEY not set: scheduled briefs use rule-based picks (no AI)")
    return RulesAnalyst(settings.max_buy_picks, settings.max_avoid_picks, settings.holdings)


def _unused() -> str:  # keep json imported for payload debugging helpers
    return json.dumps({})
