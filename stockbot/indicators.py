from __future__ import annotations


def sma(v: list[float], n: int) -> float | None:
    return sum(v[-n:]) / n if len(v) >= n else None


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


def pct(a: float, b: float) -> float | None:
    return (a / b - 1) * 100 if b else None
