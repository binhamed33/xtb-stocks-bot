"""Settings from environment / .env.

Every secret (Discord token, Gemini key, data-provider keys) is read from the
environment only. Nothing is ever written back to disk or echoed to Discord.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# Large US names available on XTB, pre-filtered to avoid obviously non-permissible businesses
# (no banks, insurers, card networks, alcohol, entertainment, defence, crypto exchanges).
# Every name is still screened automatically before any recommendation (see analysis/sharia.py).
DEFAULT_UNIVERSE = (
    "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AVGO,AMD,ADBE,"
    "ORCL,CRM,CSCO,QCOM,TXN,INTC,MU,ARM,TSM,ASML,"
    "LLY,JNJ,PFE,MRK,ABBV,TMO,XOM,CVX,COST,WMT,"
    "HD,PG,KO,PEP,NKE,CAT,DE,HON,LIN,PLTR,UBER,SHOP,SMCI,NOW"
)
# Wider list used by /scan to look for beaten-down names with sound fundamentals.
DEFAULT_SCAN_UNIVERSE = DEFAULT_UNIVERSE + (
    ",CRWD,PANW,SNOW,DDOG,NET,ZS,MDB,TEAM,WDAY,INTU,ANET,KLAC,LRCX,AMAT,MRVL,ON,NXPI,ADI,"
    "ISRG,REGN,VRTX,GILD,AMGN,BMY,ZTS,DHR,SYK,MDT,ABT,"
    "LULU,TGT,LOW,TJX,SBUX,CMG,MCD,YUM,"
    "ETSY,EBAY,ADSK,SNPS,CDNS,FTNT,OKTA,TTD,"
    "RIVN,LCID,ENPH,SEDG,FSLR,PLUG,"
    "FDX,UPS,UNP,CSX,GE,MMM,EMR,ETN,PH,ITW,"
    "SLB,HAL,OXY,COP,EOG,DVN,"
    "MRNA,BNTX,ILMN,TDOC,PTON,ZM,DOCU,U"
)

SHARIA_METHODS = ("AAOIFI", "MSCI", "FTSE")


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


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(dict.fromkeys(t.strip().upper() for t in (_env(name, default) or "").split(",") if t.strip()))


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
    # --- delivery ---
    discord_webhook: str | None
    discord_bot_token: str | None
    discord_channel_id: int | None
    discord_guild_id: int | None
    admin_user_ids: frozenset[int]

    # --- AI (Gemini only) ---
    gemini_api_key: str | None
    gemini_model: str
    gemini_daily_call_limit: int

    # --- data providers (all optional except Yahoo) ---
    fmp_api_key: str | None
    finnhub_api_key: str | None
    alphavantage_api_key: str | None
    sec_user_agent: str

    # --- universe / schedule ---
    universe: tuple[str, ...]
    scan_universe: tuple[str, ...]
    holdings: tuple[Holding, ...]
    user_tz: ZoneInfo
    premarket_minutes_before_open: int
    send_midday: bool
    midday_hour_et: int
    morning_hour_local: int
    max_buy_picks: int
    max_sell_picks: int
    max_avoid_picks: int
    portfolio_size: float
    risk_per_trade_pct: float

    # --- sharia ---
    sharia_method: str
    halal_only: bool
    halal_whitelist: frozenset[str]
    halal_blacklist: frozenset[str]
    sharia_rescreen_days: int

    # --- storage ---
    state_dir: Path
    db_path: Path
    log_file: Path | None
    language: str

    # --- alerts ---
    alert_drop_pct: float
    alert_check_minutes: int

    # --- web (tdawl.riyami.om) ---
    web_host: str
    web_port: int
    web_public_url: str              # e.g. https://tdawl.riyami.om (used for the OAuth redirect)
    web_secret: str                  # signs the session cookie; generated into state/ if missing
    web_auth: str                    # discord | code | open
    web_access_code: str | None
    discord_client_id: str | None
    discord_client_secret: str | None
    web_rate_per_minute: int

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.gemini_api_key)


def _web_secret(state_dir: Path) -> str:
    """WEB_SECRET from the environment, else a random one persisted in state/ so sessions survive restarts."""
    env = _env("WEB_SECRET")
    if env:
        return env
    p = state_dir / "web_secret"
    try:
        if p.exists():
            return p.read_text().strip()
        import secrets

        state_dir.mkdir(parents=True, exist_ok=True)
        val = secrets.token_urlsafe(48)
        p.write_text(val)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return val
    except OSError:
        import secrets

        return secrets.token_urlsafe(48)


def load_settings() -> Settings:
    universe = _csv("STOCK_UNIVERSE", DEFAULT_UNIVERSE)
    if not universe:
        raise ConfigError("STOCK_UNIVERSE is empty")
    if len(universe) > 80:
        raise ConfigError("STOCK_UNIVERSE: keep it to 80 tickers or fewer")
    scan_universe = _csv("SCAN_UNIVERSE", DEFAULT_SCAN_UNIVERSE) or universe
    try:
        tz = ZoneInfo(_env("USER_TZ", "Asia/Muscat") or "Asia/Muscat")
    except Exception as exc:  # noqa: BLE001
        raise ConfigError("USER_TZ must be a valid IANA timezone, e.g. Asia/Muscat") from exc
    method = (_env("SHARIA_METHOD", "AAOIFI") or "AAOIFI").upper()
    if method not in SHARIA_METHODS:
        raise ConfigError(f"SHARIA_METHOD must be one of {', '.join(SHARIA_METHODS)}")
    state_dir = Path(_env("BOT_STATE_DIR", "./state") or "./state")
    log_raw = _env("BOT_LOG_FILE")
    admins = frozenset(int(x) for x in (_env("ADMIN_USER_IDS") or "").replace(";", ",").split(",") if x.strip().isdigit())
    chan = _env("DISCORD_CHANNEL_ID")
    guild = _env("DISCORD_GUILD_ID")
    web_auth = (_env("WEB_AUTH") or ("discord" if _env("DISCORD_CLIENT_ID") else "code" if _env("WEB_ACCESS_CODE") else "open")).lower()
    if web_auth not in ("discord", "code", "open"):
        raise ConfigError("WEB_AUTH must be discord, code or open")
    if web_auth == "discord" and not (_env("DISCORD_CLIENT_ID") and _env("DISCORD_CLIENT_SECRET")):
        raise ConfigError("WEB_AUTH=discord needs DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET")
    return Settings(
        discord_webhook=_env("DISCORD_WEBHOOK_URL"),
        discord_bot_token=_env("DISCORD_BOT_TOKEN"),
        discord_channel_id=int(chan) if chan and chan.isdigit() else None,
        discord_guild_id=int(guild) if guild and guild.isdigit() else None,
        admin_user_ids=admins,
        gemini_api_key=_env("GEMINI_API_KEY"),
        gemini_model=_env("GEMINI_MODEL", "gemini-3-flash-preview") or "gemini-3-flash-preview",
        gemini_daily_call_limit=max(1, _int("GEMINI_DAILY_CALL_LIMIT", 200)),
        fmp_api_key=_env("FMP_API_KEY"),
        finnhub_api_key=_env("FINNHUB_API_KEY"),
        alphavantage_api_key=_env("ALPHAVANTAGE_API_KEY"),
        sec_user_agent=_env("SEC_USER_AGENT", "xtb-stocks-bot contact@example.com") or "xtb-stocks-bot contact@example.com",
        universe=universe,
        scan_universe=scan_universe,
        holdings=parse_holdings(_env("HOLDINGS")),
        user_tz=tz,
        premarket_minutes_before_open=max(5, min(_int("PREMARKET_MINUTES_BEFORE_OPEN", 45), 180)),
        send_midday=_bool("SEND_MIDDAY_UPDATE", True),
        midday_hour_et=max(10, min(_int("MIDDAY_HOUR_ET", 12), 15)),
        morning_hour_local=max(0, min(_int("MORNING_HOUR_LOCAL", 9), 23)),
        max_buy_picks=max(1, min(_int("MAX_BUY_PICKS", 5), 10)),
        max_sell_picks=max(0, min(_int("MAX_SELL_PICKS", 5), 10)),
        max_avoid_picks=max(0, min(_int("MAX_AVOID_PICKS", 5), 10)),
        portfolio_size=max(100.0, _float("PORTFOLIO_SIZE", 10_000.0)),
        risk_per_trade_pct=max(0.1, min(_float("RISK_PER_TRADE_PCT", 1.0), 5.0)),
        sharia_method=method,
        halal_only=_bool("HALAL_ONLY", True),
        halal_whitelist=frozenset(_csv("HALAL_WHITELIST")),
        halal_blacklist=frozenset(_csv("HALAL_BLACKLIST")),
        sharia_rescreen_days=max(1, _int("SHARIA_RESCREEN_DAYS", 30)),
        state_dir=state_dir,
        db_path=Path(_env("DB_PATH") or str(state_dir / "stockbot.sqlite3")),
        log_file=Path(log_raw) if log_raw else None,
        language=_env("LANGUAGE", "ar") or "ar",
        alert_drop_pct=max(0.5, _float("ALERT_DROP_PCT", 5.0)),
        alert_check_minutes=max(5, _int("ALERT_CHECK_MINUTES", 30)),
        web_host=_env("WEB_HOST", "127.0.0.1") or "127.0.0.1",
        web_port=max(1, _int("WEB_PORT", 8787)),
        web_public_url=(_env("WEB_PUBLIC_URL", "https://tdawl.riyami.om") or "https://tdawl.riyami.om").rstrip("/"),
        web_secret=_web_secret(state_dir),
        web_auth=web_auth,
        web_access_code=_env("WEB_ACCESS_CODE"),
        discord_client_id=_env("DISCORD_CLIENT_ID"),
        discord_client_secret=_env("DISCORD_CLIENT_SECRET"),
        web_rate_per_minute=max(5, _int("WEB_RATE_PER_MINUTE", 30)),
    )
