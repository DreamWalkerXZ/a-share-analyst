from unittest.mock import MagicMock

from src.agent.subgraph import (
    PREFETCH_ACTIONS,
    _round_collected,
    auto_derive_metrics,
    build_data_collection_subgraph,
    prefetch_core_data,
)


def test_prefetch_actions_has_six_entries():
    assert len(PREFETCH_ACTIONS) == 12
    assert "get_income_statement_quarterly" in PREFETCH_ACTIONS
    assert "get_balance_sheet_report" in PREFETCH_ACTIONS
    assert "get_financial_indicators_em" in PREFETCH_ACTIONS
    assert "get_peer_valuation" in PREFETCH_ACTIONS
    assert "get_profit_forecast_eps" in PREFETCH_ACTIONS
    assert "get_dividend_history_cninfo" in PREFETCH_ACTIONS
    assert "get_cashflow_report" in PREFETCH_ACTIONS


def test_prefetch_core_data_calls_all_actions(mocker):
    mock_tool = MagicMock()
    mock_tool._run.return_value = '[{"REPORT_DATE": "2025-12-31", "VALUE": 100}]'
    mocker.patch("src.agent.subgraph.structured_data_tool", mock_tool)
    results = prefetch_core_data(stock_code="600519")
    # financial_indicators_em is called twice (by_report + quarterly), rest once each
    assert mock_tool._run.call_count == len(PREFETCH_ACTIONS) + 1  # 11 + 1 = 12
    assert "get_income_statement_quarterly" in results
    assert "get_financial_indicators_em_by_report" in results
    assert "get_financial_indicators_em_quarterly" in results


def test_subgraph_is_compilable():
    graph = build_data_collection_subgraph()
    assert graph is not None


def test_auto_derive_expense_ratios():
    collected = {
        "贵州茅台_2025Q4_营业总收入": {
            "label": "营业总收入", "value": 411.5, "unit": "亿元",
            "period": "2025Q4", "source": "test", "raw_field": "", "notes": "",
        },
        "贵州茅台_2025Q4_销售费用": {
            "label": "销售费用", "value": 27.7, "unit": "亿元",
            "period": "2025Q4", "source": "test", "raw_field": "", "notes": "",
        },
    }
    derived = auto_derive_metrics("贵州茅台", collected)
    key = "贵州茅台_2025Q4_销售费用率"
    assert key in derived
    assert derived[key]["value"] == round(27.7 / 411.5 * 100, 2)
    assert derived[key]["unit"] == "%"
    assert derived[key]["source"] == "auto_derived"


def test_auto_derive_dividend_payout_ratio():
    collected = {
        "贵州茅台_2025年_基本每股收益": {
            "label": "基本每股收益（EPS）", "value": 65.66, "unit": "元/股",
            "period": "2025年", "source": "test", "raw_field": "", "notes": "",
        },
        "贵州茅台_2025年_分红": {
            "label": "贵州茅台2025年分红", "value": 276.73, "unit": "元/股",
            "period": "2025年", "source": "test", "raw_field": "派息比例", "notes": "",
        },
        "贵州茅台_2025Q2_分红": {
            "label": "贵州茅台2025Q2分红", "value": 239.57, "unit": "元/股",
            "period": "2025Q2", "source": "test", "raw_field": "派息比例", "notes": "",
        },
    }
    derived = auto_derive_metrics("贵州茅台", collected)
    payout_key = "贵州茅台_2025年_分红率"
    assert payout_key in derived
    # (276.73/10 + 239.57/10) / 65.66 * 100 = 51.63 / 65.66 * 100 ≈ 78.6%
    assert 78.0 < derived[payout_key]["value"] < 79.0
    assert derived[payout_key]["source"] == "auto_derived"


def test_auto_derive_dividend_skips_when_no_eps():
    collected = {
        "贵州茅台_2025年_分红": {
            "label": "贵州茅台2025年分红", "value": 276.73, "unit": "元/股",
            "period": "2025年", "source": "test", "raw_field": "派息比例", "notes": "",
        },
    }
    derived = auto_derive_metrics("贵州茅台", collected)
    assert "贵州茅台_2025年_分红率" not in derived


def test_auto_derive_dividend_skips_when_no_dividend():
    collected = {
        "贵州茅台_2025年_基本每股收益": {
            "label": "基本每股收益（EPS）", "value": 65.66, "unit": "元/股",
            "period": "2025年", "source": "test", "raw_field": "", "notes": "",
        },
    }
    derived = auto_derive_metrics("贵州茅台", collected)
    assert "贵州茅台_2025年_分红率" not in derived


def test_round_collected_normalizes_ratio_to_pct():
    collected = {
        "贵州茅台_2025年_国外收入占比": {
            "label": "国外收入占比", "value": 0.03, "unit": "",
            "period": "2025年", "source": "test", "raw_field": "", "notes": "",
        },
        "贵州茅台_2025年_净利润": {
            "label": "净利润", "value": 1688.38, "unit": "亿元",
            "period": "2025年", "source": "test", "raw_field": "", "notes": "",
        },
        "贵州茅台_2025年_ROE": {
            "label": "ROE", "value": 32.5, "unit": "%",
            "period": "2025年", "source": "test", "raw_field": "", "notes": "",
        },
    }
    result = _round_collected(collected)
    assert result["贵州茅台_2025年_国外收入占比"]["value"] == 3.0
    assert result["贵州茅台_2025年_国外收入占比"]["unit"] == "%"
    assert result["贵州茅台_2025年_净利润"]["value"] == 1688.38
    assert result["贵州茅台_2025年_ROE"]["value"] == 32.5
