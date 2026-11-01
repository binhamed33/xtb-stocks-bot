"""Technical analysis: trend, momentum, volatility, volume, price structure and a trade plan.

Pure functions over daily candles. No single indicator decides: the signal
score aggregates trend, momentum, structure and volume with explicit weights.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..providers.base import Candle


# ---- primitives ------------------------------------------------------------------
def sma(v: list[float], n: int) -> float | None:
    return sum(v[-n:]) / n if len(v) >= n else None


def ema_series(v: list[float], n: int) -> list[float]:
    if len(v) < n:
        return []
    k = 2 / (n + 1)
    out = [sum(v[:n]) / n]
    for x in v[n:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def ema(v: list[float], n: int) -> float | None:
    s = ema_series(v, n)
    return s[-1] if s else None


def rsi(v: list[float], n: int = 14) -> float | None:
    if len(v) <= n:
        return None
    g = l = 0.0
    for i in range(1, n + 1):
        d = v[i] - v[i - 1]
        g += max(d, 0); l += max(-d, 0)
    ag, al = g / n, l / n
    for i in range(n + 1, len(v)):
        d = v[i] - v[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def macd(v: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float] | None:
    if len(v) < slow + signal:
        return None
    ef, es = ema_series(v, fast), ema_series(v, slow)
    ef = ef[len(ef) - len(es):]
    line = [a - b for a, b in zip(ef, es)]
    sig = ema_series(line, signal)
    if not sig:
        return None
    return line[-1], sig[-1], line[-1] - sig[-1]


def bollinger(v: list[float], n: int = 20, k: float = 2.0) -> tuple[float, float, float] | None:
    if len(v) < n:
        return None
    w = v[-n:]
    m = sum(w) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
    return m - k * sd, m, m + k * sd


def atr(c: list[Candle], n: int = 14) -> float | None:
    if len(c) <= n:
        return None
    trs = [max(x.high - x.low, abs(x.high - p.close), abs(x.low - p.close)) for p, x in zip(c[:-1], c[1:])]
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def vwap(c: list[Candle], n: int = 20) -> float | None:
    w = c[-n:]
    vol = sum(x.volume for x in w)
    if not w or vol <= 0:
        return None
    return sum(((x.high + x.low + x.close) / 3) * x.volume for x in w) / vol


def swing_levels(c: list[Candle], lookback: int = 120, window: int = 5) -> tuple[list[float], list[float]]:
    """Swing highs (resistance) and swing lows (support) from local extrema."""
    w = c[-lookback:]
    highs, lows = [], []
    for i in range(window, len(w) - window):
        seg = w[i - window:i + window + 1]
        if w[i].high == max(x.high for x in seg):
            highs.append(w[i].high)
        if w[i].low == min(x.low for x in seg):
            lows.append(w[i].low)
    return sorted(set(round(x, 2) for x in highs)), sorted(set(round(x, 2) for x in lows))


def cross_recent(fast: list[float], slow: list[float], days: int = 15) -> str | None:
    """'golden' / 'death' if the fast line crossed the slow one within `days`."""
    n = min(len(fast), len(slow))
    if n < days + 1:
        return None
    f, s = fast[-n:], slow[-n:]
    for i in range(n - days, n):
        if f[i - 1] <= s[i - 1] and f[i] > s[i]:
            return "golden"
        if f[i - 1] >= s[i - 1] and f[i] < s[i]:
            return "death"
    return None


# ---- result -----------------------------------------------------------------------
@dataclass
class TechnicalView:
    price: float
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    rsi14: float | None = None
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    bb_low: float | None = None
    bb_mid: float | None = None
    bb_high: float | None = None
    atr14: float | None = None
    atr_pct: float | None = None
    vwap20: float | None = None
    vol_ratio: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    from_52w_high_pct: float | None = None
    change_1d: float | None = None
    change_1w: float | None = None
    change_1m: float | None = None
    change_3m: float | None = None
    change_6m: float | None = None
    change_1y: float | None = None
    trend_short: str = "unknown"
    trend_medium: str = "unknown"
    trend_long: str = "unknown"
    cross: str | None = None
    supports: list[float] = field(default_factory=list)
    resistances: list[float] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    score: int = 50                      # 0-100 bullish
    confidence: float = 0.5              # 0-1 how many signals agree
    # trade plan
    entry_low: float | None = None
    entry_high: float | None = None
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    reward_risk: float | None = None
    plan_note: str = ""
    signals: list[str] = field(default_factory=list)

    def summary_ar(self) -> str:
        t = {"up": "صاعد", "down": "هابط", "sideways": "عرضي", "unknown": "غير محدد"}
        return (f"قصير: {t[self.trend_short]} · متوسط: {t[self.trend_medium]} · طويل: {t[self.trend_long]}"
                + (f" · {'تقاطع ذهبي' if self.cross == 'golden' else 'تقاطع موت'}" if self.cross else ""))


def _pct(a: float, b: float | None) -> float | None:
    return None if not b else (a / b - 1) * 100


def analyze(candles: list[Candle]) -> TechnicalView | None:
    if len(candles) < 30:
        return None
    closes = [c.close for c in candles]
    vols = [c.volume for c in candles]
    p = closes[-1]
    v = TechnicalView(price=p)
    v.ema20, v.ema50, v.ema200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
    v.sma50, v.sma200 = sma(closes, 50), sma(closes, 200)
    v.rsi14 = rsi(closes, 14)
    m = macd(closes)
    if m:
        v.macd_line, v.macd_signal, v.macd_hist = m
    bb = bollinger(closes)
    if bb:
        v.bb_low, v.bb_mid, v.bb_high = bb
    v.atr14 = atr(candles)
    v.atr_pct = v.atr14 / p * 100 if v.atr14 else None
    v.vwap20 = vwap(candles)
    if len(vols) >= 21 and sum(vols[-21:-1]) > 0:
        v.vol_ratio = vols[-1] / (sum(vols[-21:-1]) / 20)
    yr = candles[-252:]
    v.high_52w, v.low_52w = max(c.high for c in yr), min(c.low for c in yr)
    v.from_52w_high_pct = _pct(p, v.high_52w)
    for attr, n in (("change_1d", 1), ("change_1w", 5), ("change_1m", 21), ("change_3m", 63), ("change_6m", 126), ("change_1y", 252)):
        setattr(v, attr, _pct(p, closes[-1 - n]) if len(closes) > n else None)

    # trends
    def trend(fast: float | None, slow: float | None, chg: float | None) -> str:
        if fast is None or slow is None:
            return "unknown"
        if fast > slow * 1.005 and p > fast and (chg or 0) > 0:
            return "up"
        if fast < slow * 0.995 and p < fast and (chg or 0) < 0:
            return "down"
        return "sideways"
    v.trend_short = trend(v.ema20, v.ema50, v.change_1m)
    v.trend_medium = trend(v.ema50, v.ema200, v.change_3m) if v.ema200 else trend(v.ema50, v.sma50, v.change_3m)
    if v.ema200:
        v.trend_long = "up" if p > v.ema200 and (v.change_6m or 0) > 0 else ("down" if p < v.ema200 and (v.change_6m or 0) < 0 else "sideways")
    e50s, e200s = ema_series(closes, 50), ema_series(closes, 200)
    v.cross = cross_recent(e50s, e200s, 20) if e200s else None

    # structure
    highs, lows = swing_levels(candles)
    v.resistances = [x for x in highs if x > p][:3]
    v.supports = [x for x in reversed(lows) if x < p][:3]
    if v.resistances == [] and highs:
        v.resistances = [max(highs)]
    # patterns
    if v.high_52w and p >= v.high_52w * 0.995 and (v.vol_ratio or 0) > 1.3:
        v.patterns.append("breakout_52w_high")
    if highs and len(candles) > 2 and candles[-2].close < max(x for x in highs if x < p * 1.02) if any(x < p * 1.02 for x in highs) else False:
        pass
    if v.ema20 and v.ema50 and v.trend_medium == "up" and abs(p - v.ema20) / p < 0.02 and (v.change_1w or 0) < 0:
        v.patterns.append("pullback_to_ema20_in_uptrend")
    if v.rsi14 is not None and v.rsi14 < 30 and v.trend_long == "up":
        v.patterns.append("oversold_in_uptrend")
    if v.bb_low and p < v.bb_low:
        v.patterns.append("below_lower_bollinger")
    if v.cross == "golden":
        v.patterns.append("golden_cross")
    if v.cross == "death":
        v.patterns.append("death_cross")
    if v.ema50 and v.trend_long == "down" and p > v.ema50 and (v.change_1m or 0) > 8:
        v.patterns.append("possible_trend_reversal")
    if v.change_1d is not None and v.change_1d < -7 and (v.vol_ratio or 0) > 2:
        v.patterns.append("high_volume_selloff")

    # score: trend 40, momentum 25, structure 20, volume 15
    sc = 50.0
    agree = 0
    total = 0
    for t in (v.trend_short, v.trend_medium, v.trend_long):
        if t == "up":
            sc += 13; agree += 1
        elif t == "down":
            sc -= 13
        total += 1
    if v.rsi14 is not None:
        total += 1
        if 45 <= v.rsi14 <= 65:
            sc += 8; agree += 1
        elif v.rsi14 > 75:
            sc -= 8
        elif v.rsi14 < 30:
            sc -= 3
    if v.macd_hist is not None:
        total += 1
        if v.macd_hist > 0:
            sc += 8; agree += 1
        else:
            sc -= 8
    if v.supports and v.resistances:
        total += 1
        room_up = (v.resistances[0] - p) / p
        room_dn = (p - v.supports[0]) / p
        if room_up > room_dn * 1.5:
            sc += 6; agree += 1
        elif room_dn > room_up * 1.5:
            sc -= 6
    if v.vol_ratio is not None:
        total += 1
        if v.vol_ratio > 1.3 and (v.change_1d or 0) > 0:
            sc += 6; agree += 1
        elif v.vol_ratio > 1.3 and (v.change_1d or 0) < 0:
            sc -= 6
    if "golden_cross" in v.patterns:
        sc += 5
    if "death_cross" in v.patterns:
        sc -= 5
    v.score = int(max(0, min(100, round(sc))))
    v.confidence = round(agree / total, 2) if total else 0.0

    # trade plan (long only - cash equities)
    if v.atr14:
        sup = v.supports[0] if v.supports else p - 2 * v.atr14
        stop = min(sup - 0.3 * v.atr14, p - 1.5 * v.atr14)
        stop = max(stop, p * 0.85)
        v.stop = round(stop, 2)
        v.entry_low, v.entry_high = round(p - 0.5 * v.atr14, 2), round(p + 0.3 * v.atr14, 2)
        risk = p - v.stop
        res = v.resistances[0] if v.resistances else None
        t1 = res if res and res > p + 1.5 * risk else p + 2 * risk
        v.target1 = round(t1, 2)
        v.target2 = round(max(t1 + 1.5 * risk, (v.resistances[1] if len(v.resistances) > 1 else t1 + 2 * risk)), 2)
        v.reward_risk = round((v.target1 - p) / risk, 2) if risk > 0 else None
        v.plan_note = "خطة طويلة الأجل: الدخول على مراحل داخل المنطقة، الوقف تحت الدعم الأقرب."
    v.signals = _signals(v)
    return v


def _signals(v: TechnicalView) -> list[str]:
    s = []
    if v.ema20 and v.ema50 and v.ema200:
        if v.price > v.ema20 > v.ema50 > v.ema200:
            s.append("السعر فوق EMA 20/50/200 مرتبة تصاعدياً: اتجاه صاعد مكتمل")
        elif v.price < v.ema20 < v.ema50 < v.ema200:
            s.append("السعر تحت EMA 20/50/200: اتجاه هابط مكتمل")
    if v.rsi14 is not None:
        s.append(f"RSI {v.rsi14:.0f}: " + ("تشبع بيعي" if v.rsi14 < 30 else "تشبع شرائي" if v.rsi14 > 70 else "منطقة محايدة"))
    if v.macd_hist is not None:
        s.append("MACD " + ("إيجابي (زخم صاعد)" if v.macd_hist > 0 else "سلبي (زخم هابط)"))
    if v.bb_low and v.bb_high:
        if v.price > v.bb_high:
            s.append("فوق نطاق بولينجر العلوي: تمدد")
        elif v.price < v.bb_low:
            s.append("تحت نطاق بولينجر السفلي: انضغاط بيعي")
    if v.vol_ratio is not None:
        s.append(f"الحجم {v.vol_ratio:.1f}× متوسط 20 يوم")
    if v.from_52w_high_pct is not None:
        s.append(f"{v.from_52w_high_pct:+.1f}% من قمة 52 أسبوع")
    for pat in v.patterns:
        s.append({"breakout_52w_high": "اختراق قمة 52 أسبوع بحجم", "pullback_to_ema20_in_uptrend": "ارتداد إلى EMA20 داخل اتجاه صاعد",
                  "oversold_in_uptrend": "تشبع بيعي داخل اتجاه صاعد طويل", "below_lower_bollinger": "إغلاق تحت بولينجر السفلي",
                  "golden_cross": "تقاطع ذهبي EMA50/200 حديث", "death_cross": "تقاطع موت EMA50/200 حديث",
                  "possible_trend_reversal": "انعكاس محتمل للاتجاه الهابط", "high_volume_selloff": "بيع عنيف بحجم مرتفع"}.get(pat, pat))
    return s
