"""Massive.com (formerly Polygon.io) data source for the EP backtest.

Needs MASSIVE_API_KEY in the environment or in a `.env` file in this strategy's folder or at the
repo root (ignored by Git). Every response is cached as JSON under the cache directory (default
`data/massive_cache` in this strategy's folder, also ignored), so reruns don't download again.
Uses only the standard library.
"""
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import json
import os
import re
import time

ET = ZoneInfo("America/New_York")
STRATEGY_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
CASH_MERGERS = {(event["ticker"], event["effective_date"]): event
                for event in json.loads(Path(__file__).with_name("cash_mergers.json").read_text(encoding="utf-8"))}


def api_key():
    key = os.environ.get("MASSIVE_API_KEY")
    for env in (STRATEGY_DIR / ".env", REPO_ROOT / ".env"):
        if not key and env.exists():
            m = re.search(r"^\s*MASSIVE_API_KEY\s*=\s*[\"']?([^\"'\s]+)", env.read_text(encoding="utf-8"), re.M)
            key = m and m.group(1)
    if not key:
        raise SystemExit("Set MASSIVE_API_KEY in the environment or in .env (this folder or the repo root).")
    return key


def add_days(d, n):
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def et(ms):
    t = datetime.fromtimestamp(ms / 1000, ET)
    return t.date().isoformat(), t.hour * 60 + t.minute


class MassiveSource:
    def __init__(self, start, end, cache_dir=None):
        self.start, self.end = start, end
        self.cache = Path(cache_dir) if cache_dir else STRATEGY_DIR / "data" / "massive_cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.key = None
        self._grouped, self._universe = {}, {}

    def cash_merger(self, ticker, day):
        """Return only explicitly verified pre-market share-cancellation events."""
        return CASH_MERGERS.get((ticker, day))

    def _cache_file(self, path):
        file = (self.cache / (re.sub(r"[^A-Za-z0-9._-]+", "_", path)[:200] + ".json")).resolve()
        # Cache names reach ~200 characters; Windows needs the extended-length prefix past 260.
        return Path("\\\\?\\" + str(file)) if os.name == "nt" else file

    def _api(self, path):
        file = self._cache_file(path)
        if file.exists():
            value = json.loads(file.read_text(encoding="utf-8"))
            self._check_response(value, path, cached=True)
            return value
        self.key = self.key or api_key()
        for attempt in range(5):
            try:
                req = Request(f"https://api.massive.com{path}", headers={"Authorization": f"Bearer {self.key}"})
                with urlopen(req, timeout=60) as res:
                    value = json.loads(res.read())
            except HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Massive HTTP {e.code} for {path.split('?')[0]}; no data cached") from None
            self._check_response(value, path)
            file.write_text(json.dumps(value), encoding="utf-8")
            return value
        raise RuntimeError(f"gave up on {path.split('?')[0]}")

    @staticmethod
    def _check_response(value, path, *, cached=False):
        status = value.get("status") if isinstance(value, dict) else "INVALID RESPONSE"
        if status not in (None, "OK", "DELAYED") or not isinstance(value, dict) or value.get("error"):
            origin = "Cached Massive" if cached else "Massive"
            action = "; remove the failed cache entry and retry" if cached else "; no data cached"
            raise RuntimeError(f"{origin} response error {status} for {path.split('?')[0]}{action}")

    def _grouped_day(self, d):
        if d not in self._grouped:
            j = self._api(f"/v2/aggs/grouped/locale/us/market/stocks/{d}?adjusted=true")
            self._grouped[d] = {r["T"]: r for r in (j.get("results") or [])}
        return self._grouped[d]

    def trading_days(self, start, end):
        out, d = [], start
        while d <= end:
            if date.fromisoformat(d).weekday() < 5 and self._grouped_day(d):
                out.append(d)
            d = add_days(d, 1)
        return out

    def previous_trading_day(self, day):
        d = add_days(day, -1)
        while not self._grouped_day(d):
            d = add_days(d, -1)
        return d

    def _stocks(self, day):
        month = day[:7] + "-01"
        if month not in self._universe:
            names = set()
            for kind in ("CS", "ADRC"):
                url = f"/v3/reference/tickers?market=stocks&type={kind}&date={month}&limit=1000"
                while url:
                    j = self._api(url)
                    names.update(r["ticker"] for r in (j.get("results") or []))
                    nxt = j.get("next_url")
                    url = re.sub(r"^https://api\.(massive\.com|polygon\.io)", "", nxt) if nxt else None
            self._universe[month] = names
        return self._universe[month]

    def gappers(self, day, prev_day, settings):
        today, prev, stocks = self._grouped_day(day), self._grouped_day(prev_day), self._stocks(day)
        out = []
        for t, r in today.items():
            p = prev.get(t)
            if t in stocks and p and r["o"] >= settings.min_price and (r["o"] / p["c"] - 1) * 100 >= settings.min_gap_pct \
                    and p["c"] * p["v"] >= settings.min_dollar_volume:
                out.append((t, p["c"]))
        return out

    def market_cap(self, ticker, day):
        return (self._api(f"/v3/reference/tickers/{quote(ticker, safe='')}?date={day[:7]}-01").get("results") or {}).get("market_cap")

    def daily(self, ticker):
        j = self._api(f"/v2/aggs/ticker/{quote(ticker, safe='')}/range/1/day/{add_days(self.start, -420)}/{self.end}?adjusted=true&sort=asc&limit=50000")
        return [{"date": et(b["t"])[0], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]} for b in (j.get("results") or [])]

    def intraday(self, ticker, day):
        j = self._api(f"/v2/aggs/ticker/{quote(ticker, safe='')}/range/5/minute/{day}/{day}?adjusted=true&sort=asc&limit=50000")
        bars = []
        for b in j.get("results") or []:
            d, m = et(b["t"])
            if d == day and 240 <= m < 960:
                bars.append({"t": b["t"], "min": m, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
        return bars
