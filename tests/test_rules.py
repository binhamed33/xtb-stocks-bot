from stockbot.config import Holding
from stockbot.data import snapshot_from_rows
from stockbot.rules import RulesAnalyst


def trend(t, up=True, n=300):
    # two steps forward, one back: a realistic trend with RSI around 60 (or 40 when down)
    a, b = (0.3, -0.2) if up else (-0.6, 0.2)
    closes, c = [], 100.0
    for i in range(n):
        c += a if i % 2 == 0 else b
        closes.append(c)
    rows = [{"ts": i, "open": x, "high": x + 1, "low": x - 1, "close": x, "volume": 1e6} for i, x in enumerate(closes)]
    return snapshot_from_rows(t, rows)


def test_rules_pick_uptrend_and_avoid_earnings():
    up, down = trend("UP"), trend("DOWN", up=False)
    earn = trend("ERN"); earn.days_to_earnings = 1; earn.earnings_date = "2026-11-03"
    a = RulesAnalyst(holdings=(Holding("DOWN", 1, 100),))
    b = a.daily_brief([up, down, earn], {"SP500_chg_pct": 0.3, "VIX_last": 18}, "2026-11-02")
    tickers = [p.ticker for p in b.buys]
    assert "UP" in tickers and "ERN" not in tickers and "DOWN" not in tickers
    buy = b.buys[0]
    assert buy.stop_loss < up.price < buy.target and (buy.target - up.price) > 2 * (up.price - buy.stop_loss)
    assert any(p.ticker == "ERN" for p in b.avoid)
    assert b.holdings_advice[0].ticker == "DOWN" and b.holdings_advice[0].action == "SELL"
    assert b.top_idea and b.key_events
