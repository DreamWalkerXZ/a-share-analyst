import pandas as pd
import pytest
from unittest.mock import MagicMock

from src.tools.structured_data import StructuredDataTool, INTERFACE_MAP


def test_interface_map_contains_required_actions():
    required = [
        "get_income_statement_quarterly",
        "get_income_statement_report",
        "get_balance_sheet_report",
        "get_cashflow_quarterly",
        "get_cashflow_report",
        "get_financial_indicators_em",
        "get_main_business_breakdown",
        "get_peer_valuation",
        "get_profit_forecast_eps",
        "get_research_reports",
        "fetch_url_as_markdown",
    ]
    for action in required:
        assert action in INTERFACE_MAP, f"Missing: {action}"


def test_run_akshare_returns_json_string(mocker):
    tool = StructuredDataTool()
    mock_df = pd.DataFrame({
        "REPORT_DATE": ["2025-12-31"],
        "TOTAL_OPERATE_INCOME": [42358000000.0],
    })
    mocker.patch("akshare.stock_profit_sheet_by_quarterly_em", return_value=mock_df)
    result = tool._run(action="get_income_statement_quarterly", params={"symbol": "SH600519"})
    assert "TOTAL_OPERATE_INCOME" in result
    assert "42358000000" in result


def test_fetch_url_as_markdown(mocker):
    tool = StructuredDataTool()
    mock_resp = MagicMock()
    mock_resp.text = "# 茅台年报\n\n内容摘要"
    mock_resp.raise_for_status = MagicMock()
    mocker.patch("requests.get", return_value=mock_resp)
    result = tool._run(action="fetch_url_as_markdown", params={"url": "https://example.com/r.pdf"})
    assert "茅台年报" in result


def test_unknown_action_raises():
    tool = StructuredDataTool()
    with pytest.raises(ValueError, match="未知 action"):
        tool._run(action="get_nonexistent_data", params={})
