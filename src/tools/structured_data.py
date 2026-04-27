from typing import Any

import requests
import akshare as ak
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from src.utils.data_cache import get_cached, set_cached

# Each entry: callable that accepts a params dict and returns a DataFrame.
# "fetch_url_as_markdown" is handled separately.
INTERFACE_MAP: dict[str, Any] = {
    # Core financials
    "get_balance_sheet_report": lambda p: ak.stock_balance_sheet_by_report_em(**p),
    "get_income_statement_report": lambda p: ak.stock_profit_sheet_by_report_em(**p),
    "get_income_statement_quarterly": lambda p: ak.stock_profit_sheet_by_quarterly_em(**p),
    "get_cashflow_report": lambda p: ak.stock_cash_flow_sheet_by_report_em(**p),
    "get_cashflow_quarterly": lambda p: ak.stock_cash_flow_sheet_by_quarterly_em(**p),
    "get_balance_sheet_sina": lambda p: ak.stock_financial_report_sina(**p),
    # Financial indicators
    "get_financial_indicators_em": lambda p: ak.stock_financial_analysis_indicator_em(**p),
    "get_financial_indicators_sina": lambda p: ak.stock_financial_analysis_indicator(**p),
    # Business breakdown
    "get_main_business_breakdown": lambda p: ak.stock_zygc_em(**p),
    "get_main_business_profile": lambda p: ak.stock_zyjs_ths(**p),
    # Peer comparison
    "get_peer_valuation": lambda p: ak.stock_zh_valuation_comparison_em(**p),
    "get_peer_dupont": lambda p: ak.stock_zh_dupont_comparison_em(**p),
    "get_peer_scale": lambda p: ak.stock_zh_scale_comparison_em(**p),
    # Valuation & dividends
    "get_spot_valuation": lambda p: ak.stock_individual_spot_xq(**p),
    "get_dividend_history_cninfo": lambda p: ak.stock_dividend_cninfo(**p),
    "get_dividend_history_sina": lambda p: ak.stock_history_dividend_detail(**p),
    # Profit forecasts
    "get_profit_forecast_eps": lambda p: ak.stock_profit_forecast_ths(
        symbol=p["symbol"], indicator="预测年报每股收益"
    ),
    "get_profit_forecast_net_profit": lambda p: ak.stock_profit_forecast_ths(
        symbol=p["symbol"], indicator="预测年报净利润"
    ),
    "get_profit_forecast_institutions": lambda p: ak.stock_profit_forecast_ths(
        symbol=p["symbol"], indicator="业绩预测详表-机构"
    ),
    "get_profit_forecast_detailed": lambda p: ak.stock_profit_forecast_ths(
        symbol=p["symbol"], indicator="业绩预测详表-详细指标预测"
    ),
    # Notices & research
    "get_notices_individual": lambda p: ak.stock_individual_notice_report(**p),
    "get_research_reports": lambda p: ak.stock_research_report_em(**p),
    # Industry & risk
    "get_industry_pe": lambda p: ak.stock_industry_pe_ratio_cninfo(**p),
    "get_industry_goodwill": lambda p: ak.stock_sy_hy_em(**p),
    "get_pledge_ratio": lambda p: ak.stock_gpzy_pledge_ratio_em(**p),
    # Sentiment
    "get_market_comment_overview": lambda p: ak.stock_comment_em(),
    "get_comment_rating": lambda p: ak.stock_comment_detail_zhpj_lspf_em(**p),
    "get_comment_institution": lambda p: ak.stock_comment_detail_zlkp_jgcyd_em(**p),
    # Web fetch (sentinel value; handled in _run)
    "fetch_url_as_markdown": None,
}


# Interfaces that genuinely require no params (call akshare with no arguments).
_NO_PARAMS_REQUIRED = {"get_market_comment_overview"}


class StructuredDataInput(BaseModel):
    action: str = Field(description="接口名称，如 get_income_statement_quarterly")
    params: dict = Field(
        default_factory=dict,
        description=(
            "接口参数，如 {'symbol': 'SH600519'}。"
            "除 get_market_comment_overview 外，其他接口必须传入非空 params（含 symbol 等）。"
        ),
    )


class StructuredDataTool(BaseTool):
    name: str = "structured_data"
    description: str = (
        "从 akshare 获取结构化金融数据（返回原始 JSON），"
        "或通过 fetch_url_as_markdown 将网页/PDF 转为 Markdown。"
        "action 为接口名称，params 为接口参数（必须传入，不得为空 {}）。"
    )
    args_schema: type[BaseModel] = StructuredDataInput

    def _run(self, action: str, params: dict | None = None) -> str:  # type: ignore[override]
        params = params or {}

        if action == "fetch_url_as_markdown":
            url = params.get("url", "")
            resp = requests.get(f"https://r.jina.ai/{url}", timeout=30)
            resp.raise_for_status()
            return resp.text

        if action not in INTERFACE_MAP:
            raise ValueError(
                f"未知 action: {action!r}。可用接口：{list(INTERFACE_MAP.keys())}"
            )

        if not params and action not in _NO_PARAMS_REQUIRED:
            raise ValueError(
                f"接口 {action!r} 缺少必要参数（params 不能为空 {{}}）。"
                "请参照 system prompt 中的调用示例，传入正确的 symbol 等参数。"
                "例如：params={{\"symbol\": \"SH600519\"}}"
            )

        cached = get_cached(action, params)
        if cached is not None:
            print(f"[cache] 命中缓存：{action}({params})")
            return cached

        df = INTERFACE_MAP[action](params)
        result = df.to_json(orient="records", force_ascii=False, date_format="iso")
        set_cached(action, params, result)
        return result
