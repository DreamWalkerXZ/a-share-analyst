from unittest.mock import MagicMock, patch

import pytest

from src.agent.subgraph import PREFETCH_ACTIONS, build_data_collection_subgraph, prefetch_core_data


def test_prefetch_actions_has_six_entries():
    assert len(PREFETCH_ACTIONS) == 13
    assert "get_income_statement_quarterly" in PREFETCH_ACTIONS
    assert "get_balance_sheet_report" in PREFETCH_ACTIONS
    assert "get_financial_indicators_em" in PREFETCH_ACTIONS
    assert "get_peer_valuation" in PREFETCH_ACTIONS
    assert "get_profit_forecast_eps" in PREFETCH_ACTIONS
    assert "get_dividend_history_cninfo" in PREFETCH_ACTIONS


def test_prefetch_core_data_calls_all_actions(mocker):
    mock_tool = MagicMock()
    mock_tool._run.return_value = '[{"REPORT_DATE": "2025-12-31", "VALUE": 100}]'
    mocker.patch("src.agent.subgraph.structured_data_tool", mock_tool)
    results = prefetch_core_data(stock_code="600519")
    # financial_indicators_em is called twice (by_report + quarterly), rest once each
    assert mock_tool._run.call_count == len(PREFETCH_ACTIONS) + 1  # 13 + 1 = 14
    assert "get_income_statement_quarterly" in results
    assert "get_financial_indicators_em_by_report" in results
    assert "get_financial_indicators_em_quarterly" in results


def test_subgraph_is_compilable():
    graph = build_data_collection_subgraph()
    assert graph is not None
