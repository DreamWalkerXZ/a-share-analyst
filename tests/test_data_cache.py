import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.utils.data_cache import _cache_key, _cache_path, get_cached, set_cached, cache_stats
from src.tools.structured_data import StructuredDataTool


# ── Unit tests for cache helpers ──────────────────────────────────────────────

def test_cache_key_is_deterministic():
    k1 = _cache_key("get_income_statement_quarterly", {"symbol": "SH600519"})
    k2 = _cache_key("get_income_statement_quarterly", {"symbol": "SH600519"})
    assert k1 == k2


def test_cache_key_differs_for_different_params():
    k1 = _cache_key("get_income_statement_quarterly", {"symbol": "SH600519"})
    k2 = _cache_key("get_income_statement_quarterly", {"symbol": "SH000001"})
    assert k1 != k2


def test_cache_key_differs_for_different_actions():
    k1 = _cache_key("get_income_statement_quarterly", {"symbol": "SH600519"})
    k2 = _cache_key("get_balance_sheet_report", {"symbol": "SH600519"})
    assert k1 != k2


def test_get_cached_returns_none_when_missing(tmp_path):
    with patch("src.utils.data_cache._CACHE_DIR", tmp_path):
        result = get_cached("get_income_statement_quarterly", {"symbol": "SH600519"})
    assert result is None


def test_set_and_get_cached_round_trip(tmp_path):
    with patch("src.utils.data_cache._CACHE_DIR", tmp_path):
        set_cached("get_income_statement_quarterly", {"symbol": "SH600519"}, '{"data": 1}')
        result = get_cached("get_income_statement_quarterly", {"symbol": "SH600519"})
    assert result == '{"data": 1}'


def test_cached_data_expires(tmp_path):
    from datetime import datetime, timedelta, timezone

    action = "get_income_statement_quarterly"
    params = {"symbol": "SH600519"}
    with patch("src.utils.data_cache._CACHE_DIR", tmp_path):
        set_cached(action, params, "stale_data")
        # Manually backdate the cached_at timestamp
        path = _cache_path(_cache_key(action, params))
        path = tmp_path / path.name
        envelope = json.loads(path.read_text())
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=10)).isoformat()
        envelope["cached_at"] = old_ts
        path.write_text(json.dumps(envelope))

        result = get_cached(action, params)
    assert result is None


def test_no_cache_for_excluded_actions(tmp_path):
    with patch("src.utils.data_cache._CACHE_DIR", tmp_path):
        set_cached("fetch_url_as_markdown", {"url": "https://x.com"}, "html")
        result = get_cached("fetch_url_as_markdown", {"url": "https://x.com"})
    assert result is None


def test_disable_data_cache_env_var(tmp_path):
    with patch("src.utils.data_cache._CACHE_DIR", tmp_path):
        with patch.dict(os.environ, {"DISABLE_DATA_CACHE": "1"}):
            set_cached("get_income_statement_quarterly", {"symbol": "SH600519"}, "data")
            result = get_cached("get_income_statement_quarterly", {"symbol": "SH600519"})
    assert result is None


def test_cache_stats_empty(tmp_path):
    with patch("src.utils.data_cache._CACHE_DIR", tmp_path):
        stats = cache_stats()
    assert stats["files"] == 0


def test_cache_stats_after_write(tmp_path):
    with patch("src.utils.data_cache._CACHE_DIR", tmp_path):
        set_cached("get_income_statement_quarterly", {"symbol": "SH600519"}, "x" * 1000)
        stats = cache_stats()
    assert stats["files"] == 1
    assert stats["size_kb"] > 0


# ── Integration: StructuredDataTool uses cache ────────────────────────────────

def test_tool_writes_to_cache_on_first_call(mocker, tmp_path):
    tool = StructuredDataTool()
    mock_df = pd.DataFrame({"REPORT_DATE": ["2025-12-31"], "VALUE": [42.0]})
    mocker.patch("akshare.stock_profit_sheet_by_quarterly_em", return_value=mock_df)
    mocker.patch("src.tools.structured_data.get_cached", return_value=None)
    mock_set = mocker.patch("src.tools.structured_data.set_cached")

    tool._run(action="get_income_statement_quarterly", params={"symbol": "SH600519"})

    mock_set.assert_called_once()
    call_args = mock_set.call_args
    assert call_args[0][0] == "get_income_statement_quarterly"


def test_tool_reads_from_cache_on_second_call(mocker):
    tool = StructuredDataTool()
    cached_json = '[{"REPORT_DATE": "2025-12-31", "VALUE": 42.0}]'
    mocker.patch("src.tools.structured_data.get_cached", return_value=cached_json)
    ak_mock = mocker.patch("akshare.stock_profit_sheet_by_quarterly_em")

    result = tool._run(action="get_income_statement_quarterly", params={"symbol": "SH600519"})

    ak_mock.assert_not_called()
    assert result == cached_json
