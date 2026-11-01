"""SEC EDGAR: official filings (10-K, 10-Q, 8-K, Form 4 insider transactions).

Free, official, no key. SEC requires a descriptive User-Agent and asks for
at most 10 requests/second; we stay far below that.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request

from .base import Filing

log = logging.getLogger(__name__)
NAME = "SEC EDGAR"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

EVENT_FORMS = {
    "8-K": "material event (8-K)",
    "10-K": "annual report (10-K)",
    "10-Q": "quarterly report (10-Q)",
    "4": "insider transaction (Form 4)",
    "SC 13D": "activist / >5% stake (13D)",
    "SC 13G": "passive >5% stake (13G)",
    "DEF 14A": "proxy statement",
    "S-1": "registration / offering (S-1)",
    "424B": "prospectus / offering",
}


class SecEdgarProvider:
    name = NAME

    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._cik_map: dict[str, int] | None = None
        self._last = 0.0

    def _get(self, url: str) -> dict:
        gap = time.monotonic() - self._last
        if gap < 0.15:
            time.sleep(0.15 - gap)
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
        self._last = time.monotonic()
        return json.loads(raw.decode("utf-8"))

    def cik(self, ticker: str) -> int | None:
        if self._cik_map is None:
            try:
                data = self._get(_TICKERS_URL)
                self._cik_map = {str(v["ticker"]).upper(): int(v["cik_str"]) for v in data.values()}
            except Exception as exc:  # noqa: BLE001
                log.warning("SEC ticker map failed: %s", exc)
                self._cik_map = {}
        return self._cik_map.get(ticker.upper().replace("-", ""))

    def filings(self, ticker: str, limit: int = 10) -> list[Filing]:
        cik = self.cik(ticker)
        if cik is None:
            return []
        try:
            sub = self._get(_SUBMISSIONS_URL.format(cik=cik))
        except Exception as exc:  # noqa: BLE001
            log.warning("SEC submissions failed for %s: %s", ticker, exc)
            return []
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accs = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])
        out: list[Filing] = []
        for form, date, acc, doc in zip(forms, dates, accs, docs):
            base = form.split("/")[0]
            label = next((v for k, v in EVENT_FORMS.items() if base.startswith(k)), None)
            if label is None:
                continue
            acc_nodash = acc.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"
            out.append(Filing(form=form, filed_at=date, description=label, url=url))
            if len(out) >= limit:
                break
        return out
