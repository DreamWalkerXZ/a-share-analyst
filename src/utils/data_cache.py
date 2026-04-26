"""
File-based cache for akshare structured data.

Cache files live in data/cache/ and are keyed by a SHA-1 hash of
(action, sorted-params). Each file is a JSON envelope:

    {"cached_at": <iso-timestamp>, "data": <raw-string>}

TTL is configurable per action group. Set env var DISABLE_DATA_CACHE=1
to bypass the cache entirely (useful during development/debugging).
"""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

_CACHE_DIR = Path("data/cache")

# TTL per action group (seconds)
# Historical financials: quarterly updates → 7 days
# Analyst forecasts / industry data: daily updates → 1 day
# Real-time price / sentiment: → 1 hour
# No cache: fetch_url_as_markdown (web pages change constantly)
_TTL_7D = 7 * 86400
_TTL_1D = 86400
_TTL_1H = 3600

_ACTION_TTL: dict[str, int] = {
    # Core financials — stable historical data
    "get_balance_sheet_report":          _TTL_7D,
    "get_income_statement_report":       _TTL_7D,
    "get_income_statement_quarterly":    _TTL_7D,
    "get_cashflow_report":               _TTL_7D,
    "get_cashflow_quarterly":            _TTL_7D,
    "get_balance_sheet_sina":            _TTL_7D,
    "get_financial_indicators_em":       _TTL_7D,
    "get_financial_indicators_sina":     _TTL_7D,
    "get_main_business_breakdown":       _TTL_7D,
    "get_main_business_profile":         _TTL_7D,
    # Dividend history — very stable
    "get_dividend_history_cninfo":       _TTL_7D,
    "get_dividend_history_sina":         _TTL_7D,
    # Analyst forecasts / research — updated daily
    "get_profit_forecast_eps":           _TTL_1D,
    "get_profit_forecast_net_profit":    _TTL_1D,
    "get_profit_forecast_institutions":  _TTL_1D,
    "get_profit_forecast_detailed":      _TTL_1D,
    "get_notices_individual":            _TTL_1D,
    "get_research_reports":              _TTL_1D,
    # Peer & industry — refreshed daily
    "get_peer_valuation":                _TTL_1D,
    "get_peer_dupont":                   _TTL_1D,
    "get_peer_scale":                    _TTL_1D,
    "get_industry_pe":                   _TTL_1D,
    "get_industry_goodwill":             _TTL_1D,
    "get_pledge_ratio":                  _TTL_1D,
    # Real-time / intraday
    "get_spot_valuation":                _TTL_1H,
    "get_market_comment_overview":       _TTL_1H,
    "get_comment_rating":                _TTL_1H,
    "get_comment_institution":           _TTL_1H,
}

# Actions excluded from caching
_NO_CACHE = {"fetch_url_as_markdown"}


def _cache_key(action: str, params: dict) -> str:
    payload = json.dumps({"action": action, "params": params}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.json"


def _is_disabled() -> bool:
    return os.environ.get("DISABLE_DATA_CACHE", "").strip() in ("1", "true", "yes")


def get_cached(action: str, params: dict) -> str | None:
    """Return cached data string if fresh, else None."""
    if _is_disabled() or action in _NO_CACHE:
        return None
    ttl = _ACTION_TTL.get(action, _TTL_1D)
    path = _cache_path(_cache_key(action, params))
    if not path.exists():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(envelope["cached_at"])
        if datetime.now(tz=timezone.utc) - cached_at > timedelta(seconds=ttl):
            return None
        return envelope["data"]
    except Exception:
        return None


def set_cached(action: str, params: dict, data: str) -> None:
    """Persist data string to cache."""
    if _is_disabled() or action in _NO_CACHE:
        return
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(_cache_key(action, params))
    envelope = {
        "cached_at": datetime.now(tz=timezone.utc).isoformat(),
        "action": action,
        "params": params,
        "data": data,
    }
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")


def cache_stats() -> dict:
    """Return basic stats about the cache directory."""
    if not _CACHE_DIR.exists():
        return {"files": 0, "size_kb": 0}
    files = list(_CACHE_DIR.glob("*.json"))
    size = sum(f.stat().st_size for f in files)
    return {"files": len(files), "size_kb": round(size / 1024, 1)}
