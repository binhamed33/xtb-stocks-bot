"""Claude analysis -> structured daily brief (Arabic by default)."""

from __future__ import annotations

import json
import logging
from typing import Literal

import anthropic
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


SYSTEM = """You are the research desk of a small, careful stock investor who trades US shares
through XTB (cash stocks, no leverage, no shorting). You write in {language}.

You receive a quantitative snapshot of a fixed watch-list (prices, trend, momentum, 52-week
position, volume, valuation, earnings dates, headlines), an index overview, and the user's
holdings. You do NOT have live news beyond the headlines given; say so when it matters.

Rules:
- Be selective. A BUY needs several aligned signals and a clear invalidation. Fewer, better picks.
- Every BUY includes an entry zone, an absolute stop_loss below a real level, and a target
  with at least 2:1 reward-to-risk. Stops wider than ~8% are usually wrong for stocks.
- AVOID names with earnings in the next 2 trading days unless the thesis is specifically about it.
  Flag earnings within 7 days inside risk_note.
- SELL only applies to holdings or to names the user might own from earlier briefs: take profit
  when the target is reached or the thesis broke.
- conviction is your honest probability the idea works within the horizon. Most ideas are 0.5-0.65.
- Stay within the max counts given. Mention prices with two decimals.
- Keep every reason concrete: name the level, the indicator, the event. No filler.
- Do not invent news. Never guarantee results; this is research, not advice.
"""


class Analyst:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self.s = settings
        self.client = client or anthropic.Anthropic()

    def _system(self) -> str:
        lang = {"ar": "Arabic (Gulf-friendly, clear, no slang)", "en": "English"}.get(self.s.language, self.s.language)
        return SYSTEM.format(language=lang)

    def daily_brief(self, snaps: list[StockSnapshot], indices: dict, today: str,
                    previous_picks: list[dict] | None = None) -> DailyBrief:
        payload = {
            "date": today,
            "limits": {"max_buys": self.s.max_buy_picks, "max_sells": self.s.max_sell_picks,
                       "max_avoid": self.s.max_avoid_picks},
            "portfolio": {"size_usd": self.s.portfolio_size, "risk_per_trade_pct": self.s.risk_per_trade_pct},
            "holdings": [h.__dict__ for h in self.s.holdings],
            "previous_open_ideas": previous_picks or [],
            "indices": indices,
            "stocks": [s.as_prompt_dict() for s in snaps],
        }
        resp = self.client.messages.parse(
            model=self.s.anthropic_model, max_tokens=16000,
            system=[{"type": "text", "text": self._system(), "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"}, output_config={"effort": self.s.claude_effort},
            messages=[{"role": "user", "content": "Produce today's pre-market brief.\n\n" + json.dumps(payload, ensure_ascii=False)}],
            output_format=DailyBrief,
        )
        self._log_usage(resp)
        if resp.parsed_output is None:
            raise RuntimeError(f"no brief (stop_reason={resp.stop_reason})")
        brief = resp.parsed_output
        valid = {s.ticker for s in snaps} | {h.ticker for h in self.s.holdings}
        brief.buys = [p for p in brief.buys if p.ticker.upper() in valid and p.stop_loss and p.target][: self.s.max_buy_picks]
        brief.sells = [p for p in brief.sells if p.ticker.upper() in valid][: self.s.max_sell_picks]
        brief.avoid = [p for p in brief.avoid if p.ticker.upper() in valid][: self.s.max_avoid_picks]
        for p in brief.buys + brief.sells + brief.avoid + brief.holdings_advice:
            p.ticker = p.ticker.upper()
        return brief

    def close_summary(self, indices: dict, picks_perf: list[dict], movers: list[dict], today: str) -> CloseSummary:
        payload = {"date": today, "indices": indices, "todays_ideas_performance": picks_perf, "biggest_movers": movers}
        resp = self.client.messages.parse(
            model=self.s.anthropic_model, max_tokens=8000,
            system=[{"type": "text", "text": self._system(), "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"}, output_config={"effort": "medium"},
            messages=[{"role": "user", "content": "Write the closing-bell summary.\n\n" + json.dumps(payload, ensure_ascii=False)}],
            output_format=CloseSummary,
        )
        self._log_usage(resp)
        if resp.parsed_output is None:
            raise RuntimeError(f"no summary (stop_reason={resp.stop_reason})")
        return resp.parsed_output

    @staticmethod
    def _log_usage(resp) -> None:  # noqa: ANN001
        u = resp.usage
        log.info("Claude usage in=%s cached=%s out=%s", u.input_tokens, u.cache_read_input_tokens, u.output_tokens)
