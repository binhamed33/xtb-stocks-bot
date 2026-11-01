"""Settings from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# Large US names available on XTB, pre-filtered to avoid obviously non-permissible businesses
# (no banks, insurers, card networks, alcohol, entertainment, defence, crypto exchanges).
# Every name is still screened automatically before any recommendation (see halal.py).
DEFAULT_UNIVERSE = (
    "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AVGO,AMD,ADBE,"
    "ORCL,CRM,CSCO,QCOM,TXN,INTC,MU,ARM,TSM,ASML,"
    "LLY,JNJ,PFE,MRK,ABBV,TMO,XOM,CVX,COST,WMT,"
    "HD,PG,KO,PEP,NKE,CAT,DE,HON,LIN,PLTR,UBER,SHOP,SMCI,NOW"
)


class ConfigError(ValueError):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return default if v is None or not v.strip() else v.strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)) or default)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)) or default)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if v is None else v.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Holding:
    ticker: str
    qty: float
    avg_price: float


def parse_holdings(raw: str | None) -> tuple[Holding, ...]:
    """'AAPL:10@180.5,MSFT:5@400' -> holdings."""
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ticker, rest = part.split(":")
            qty, price = rest.split("@")
            out.append(Holding(ticker.strip().upper(), float(qty), float(price)))
        except ValueError as exc:
            raise ConfigError(f"HOLDINGS entry {part!r} must look like AAPL:10@180.5") from exc
    return tuple(out)


@dataclass(frozen=True)
class Settings:
    discord_webhook: str | None
    anthropic_model: str
    claude_effort: str
    universe: tuple[str, ...]
    holdings: tuple[Holding, ...]
    user_tz: ZoneInfo
    premarket_minutes_before_open: int
    send_midday: bool
    midday_hour_et: int
    max_buy_picks: int
    max_sell_picks: int
    max_avoid_picks: int
    portfolio_size: float
    risk_per_trade_pct: float
    state_dir: Path
    log_file: Path | None
    language: str
    halal_only: bool
    halal_whitelist: frozenset[str]
    halal_blacklist: frozenset[str]
    morning_hour_local: int

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"


def load_settings() -> Settings:
    universe = tuple(dict.fromkeys(t.strip().upper() for t in (_env("STOCK_UNIVERSE", DEFAULT_UNIVERSE) or "").split(",") if t.strip()))
    if not universe:
        raise ConfigError("STOCK_UNIVERSE is empty")
    if len(universe) > 80:
        raise ConfigError("STOCK_UNIVERSE: keep it to 80 tickers or fewer")
    effort = _env("CLAUDE_EFFORT", "high") or "high"
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ConfigError("CLAUDE_EFFORT must be low/medium/high/xhigh/max")
    try:
        tz = ZoneInfo(_env("USER_TZ", "Asia/Muscat") or "Asia/Muscat")
    except Exception as exc:  # noqa: BLE001
        raise ConfigError("USER_TZ must be a valid IANA timezone, e.g. Asia/Muscat") from exc
    log_raw = _env("BOT_LOG_FILE")
    return Settings(
        discord_webhook=_env("DISCORD_WEBHOOK_URL"),
        anthropic_model=_env("ANTHROPIC_MODEL", "claude-opus-5") or "claude-opus-5",
        claude_effort=effort,
        universe=universe,
        holdings=parse_holdings(_env("HOLDINGS")),
        user_tz=tz,
        premarket_minutes_before_open=max(5, min(_int("PREMARKET_MINUTES_BEFORE_OPEN", 45), 180)),
        send_midday=_bool("SEND_MIDDAY_UPDATE", True),
        midday_hour_et=max(10, min(_int("MIDDAY_HOUR_ET", 12), 15)),
        max_buy_picks=max(1, min(_int("MAX_BUY_PICKS", 5), 10)),
        max_sell_picks=max(0, min(_int("MAX_SELL_PICKS", 5), 10)),
        max_avoid_picks=max(0, min(_int("MAX_AVOID_PICKS", 5), 10)),
        portfolio_size=max(100.0, _float("PORTFOLIO_SIZE", 10_000.0)),
        risk_per_trade_pct=max(0.1, min(_float("RISK_PER_TRADE_PCT", 1.0), 5.0)),
        state_dir=Path(_env("BOT_STATE_DIR", "./state") or "./state"),
        log_file=Path(log_raw) if log_raw else None,
        language=_env("LANGUAGE", "ar") or "ar",
        halal_only=_bool("HALAL_ONLY", True),
        halal_whitelist=frozenset(t.strip().upper() for t in (_env("HALAL_WHITELIST") or "").split(",") if t.strip()),
        halal_blacklist=frozenset(t.strip().upper() for t in (_env("HALAL_BLACKLIST") or "").split(",") if t.strip()),
        morning_hour_local=max(0, min(_int("MORNING_HOUR_LOCAL", 9), 23)),
    )
