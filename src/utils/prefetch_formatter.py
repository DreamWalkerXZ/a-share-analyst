"""
Per-interface formatters for Phase-1 prefetch data.

Each formatter takes the raw JSON string returned by akshare (already
period-filtered) and returns a compact, human-readable text that is
much easier for the LLM to parse correctly:
  - Only key columns are included (drops ~90% of irrelevant fields)
  - Monetary values are converted from 元 to 亿元 (÷1e8)
  - Per-share metrics are explicitly labelled as "元/股" and are NOT divided
  - Column names are in Chinese with units and disambiguation notes
"""

import json
from datetime import datetime, timezone
from typing import Any


def _to_yi(v: Any) -> float | None:
    """Convert 元 → 亿元 (÷1e8). Returns None on failure."""
    try:
        return round(float(v) / 1e8, 4)
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _to_pct(v: Any) -> float | None:
    """Convert decimal ratio (0–1) → percentage (0–100)."""
    try:
        return round(float(v) * 100, 2)
    except (TypeError, ValueError):
        return None


def _ts_to_date(v: Any) -> str | None:
    """Convert Unix-ms timestamp → YYYY-MM-DD string."""
    try:
        ms = int(v)
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return str(v)[:10] if v else None


# ── Field-map helpers ─────────────────────────────────────────────────────────
# Each entry: display_name, converter, unit
# converter=_to_yi  → monetary total (元→亿元)
# converter=_to_float → already in display unit (%, 元/股, 倍, etc.)

_INCOME_STMT_FIELDS: dict[str, tuple[str, Any, str]] = {
    "TOTAL_OPERATE_INCOME": ("营业总收入",                       _to_yi,    "亿元"),
    "OPERATE_INCOME":       ("营业收入（主营业务）",              _to_yi,    "亿元"),
    "OPERATE_COST":         ("营业成本",                         _to_yi,    "亿元"),
    "OPERATE_TAX_ADD":      ("营业税金及附加",                   _to_yi,    "亿元"),
    "SALE_EXPENSE":         ("销售费用",                         _to_yi,    "亿元"),
    "MANAGE_EXPENSE":       ("管理费用",                         _to_yi,    "亿元"),
    "FINANCE_EXPENSE":      ("财务费用",                         _to_yi,    "亿元"),
    "OPERATE_PROFIT":       ("营业利润",                         _to_yi,    "亿元"),
    "TOTAL_PROFIT":         ("利润总额",                         _to_yi,    "亿元"),
    "INCOME_TAX":           ("所得税费用",                       _to_yi,    "亿元"),
    "NETPROFIT":            ("净利润（含少数股东）",              _to_yi,    "亿元"),
    "PARENT_NETPROFIT":     ("归母净利润",                       _to_yi,    "亿元"),
    "DEDUCT_PARENT_NETPROFIT": ("扣非归母净利润",                _to_yi,    "亿元"),
    "MINORITY_INTEREST":    ("少数股东损益",                     _to_yi,    "亿元"),
    "BASIC_EPS":            ("基本每股收益 [每股值，单位元/股，勿除1e8]", _to_float, "元/股"),
    "DILUTED_EPS":          ("稀释每股收益 [每股值，单位元/股，勿除1e8]", _to_float, "元/股"),
}

_BALANCE_SHEET_FIELDS: dict[str, tuple[str, Any, str]] = {
    "TOTAL_ASSETS":          ("总资产",                          _to_yi,    "亿元"),
    "TOTAL_LIABILITIES":     ("总负债",                          _to_yi,    "亿元"),
    "TOTAL_EQUITY":          ("股东权益合计",                    _to_yi,    "亿元"),
    "MONETARYFUNDS":         ("货币资金（银行存款等狭义现金，不含短期理财）", _to_yi, "亿元"),
    "ACCOUNTS_RECE":         ("应收账款",                        _to_yi,    "亿元"),
    "INVENTORY":             ("存货",                            _to_yi,    "亿元"),
    "FIXED_ASSET":           ("固定资产",                        _to_yi,    "亿元"),
    "INTANGIBLE_ASSET":      ("无形资产",                        _to_yi,    "亿元"),
    "CONTRACT_LIAB":         ("合同负债（预收款项）",             _to_yi,    "亿元"),
    "TAX_PAYABLE":           ("应交税费",                        _to_yi,    "亿元"),
    "STAFF_SALARY_PAYABLE":  ("应付职工薪酬",                    _to_yi,    "亿元"),
    "NOTES_RECE":            ("应收票据",                        _to_yi,    "亿元"),
    "PREPAYMENT":            ("预付款项",                        _to_yi,    "亿元"),
}

_CASHFLOW_FIELDS: dict[str, tuple[str, Any, str]] = {
    "SALES_SERVICES":        ("销售商品/提供劳务收到的现金",      _to_yi,    "亿元"),
    "TOTAL_OPERATE_INFLOW":  ("经营活动现金流入合计",             _to_yi,    "亿元"),
    "TOTAL_OPERATE_OUTFLOW": ("经营活动现金流出合计",             _to_yi,    "亿元"),
    "NETCASH_OPERATE":       ("经营活动现金流量净额 [总量，非每股！]", _to_yi, "亿元"),
    "PAY_ALL_TAX":           ("支付的税款",                      _to_yi,    "亿元"),
    "NETCASH_INVEST":        ("投资活动现金流量净额",             _to_yi,    "亿元"),
    "INVEST_PAY_CASH":       ("投资支付的现金",                   _to_yi,    "亿元"),
    "WITHDRAW_INVEST":       ("收回投资收到的现金",               _to_yi,    "亿元"),
    "ASSIGN_DIVIDEND_PORFIT": ("分红/偿债支付的现金",             _to_yi,    "亿元"),
    "NETCASH_FINANCE":       ("筹资活动现金流量净额",             _to_yi,    "亿元"),
    "CCE_ADD":               ("现金及现金等价物净增加额",          _to_yi,    "亿元"),
    "BEGIN_CCE":             ("期初现金及等价物余额（含货币基金，广义口径）", _to_yi, "亿元"),
    "END_CCE":               ("期末现金及等价物余额（含货币基金，广义口径）", _to_yi, "亿元"),
}

_EM_QUARTERLY_FIELDS: dict[str, tuple[str, Any, str]] = {
    "TOTALOPERATEREVE":    ("营业总收入 [总量]",                  _to_yi,    "亿元"),
    "GROSS_PROFIT":        ("毛利润 [总量]",                      _to_yi,    "亿元"),
    "PARENTNETPROFIT":     ("归母净利润 [总量]",                  _to_yi,    "亿元"),
    "DEDU_PARENT_PROFIT":  ("扣非归母净利润 [总量]",              _to_yi,    "亿元"),
    # Per-share fields — MUST NOT be divided by 1e8
    "EPSJB":               ("基本每股收益（EPS）[每股值，元/股，勿除1e8]", _to_float, "元/股"),
    "BPS":                 ("每股净资产 [每股值，元/股，勿除1e8]",  _to_float, "元/股"),
    "PER_NETCASH":         ("每股经营现金流 [每股值，元/股，勿除1e8；经营现金流总额在现金流量表中]",
                            _to_float, "元/股"),
    "PER_UNASSIGN_PROFIT": ("每股未分配利润 [每股值，元/股，勿除1e8]", _to_float, "元/股"),
    # Rate / ratio fields — already in %
    "GROSS_PROFIT_RATIO":  ("销售毛利率",                         _to_float, "%"),
    "NET_PROFIT_RATIO":    ("销售净利率",                         _to_float, "%"),
    "ROE_DILUTED":         ("净资产收益率（稀释）",                _to_float, "%"),
    "JROA":                ("总资产净利率（ROA）",                 _to_float, "%"),
    "TOTALOPERATEREVETZ":  ("营业总收入同比增速",                  _to_float, "%"),
    "PARENTNETPROFITTZ":   ("归母净利润同比增速",                  _to_float, "%"),
    "DPNP_YOY_RATIO":      ("扣非归母净利润同比增速",              _to_float, "%"),
}

# by_report uses cumulative annual data — rename YoY fields to avoid collision
# with the quarterly single-period YoY values so both survive deduplication.
_EM_BY_REPORT_FIELDS: dict[str, tuple[str, Any, str]] = {
    **{k: v for k, v in _EM_QUARTERLY_FIELDS.items()
       if k not in {"TOTALOPERATEREVETZ", "PARENTNETPROFITTZ", "DPNP_YOY_RATIO"}},
    # Renamed: "全年累计" suffix distinguishes these from single-quarter YoY
    "TOTALOPERATEREVETZ": ("营业总收入全年同比增速（累计）",         _to_float, "%"),
    "PARENTNETPROFITTZ":  ("归母净利润全年同比增速（累计）",          _to_float, "%"),
    "DPNP_YOY_RATIO":     ("扣非归母净利润全年同比增速（累计）",      _to_float, "%"),
}

_MAIN_BUSINESS_FIELDS: dict[str, tuple[str, Any, str]] = {
    "主营构成":   ("业务/产品名称",           str,    ""),
    "分类类型":   ("分类维度（产品/地区）",    str,    ""),
    "主营收入":   ("主营收入 [总量]",          _to_yi, "亿元"),
    "收入比例":   ("收入占比",                _to_pct, "%"),
    "主营成本":   ("主营成本 [总量]",          _to_yi, "亿元"),
    "毛利率":     ("毛利率",                  _to_pct, "%"),
}


# ── Core formatter ────────────────────────────────────────────────────────────

def _format_records(
    records: list[dict],
    field_map: dict[str, tuple[str, Any, str]],
    date_field: str = "REPORT_DATE",
    is_already_yi: bool = False,
) -> str:
    """Render a list of records using the given field map into readable text."""
    lines: list[str] = []
    for rec in records:
        date_val = str(rec.get(date_field, "未知报告期"))[:10]
        lines.append(f"\n【报告期：{date_val}】")
        for raw_key, (display_name, converter, unit) in field_map.items():
            raw_val = rec.get(raw_key)
            if raw_val is None or raw_val == "" or str(raw_val) in ("nan", "None", "false"):
                continue
            converted = converter(raw_val) if converter is not str else str(raw_val)
            if converted is None:
                continue
            suffix = f" {unit}" if unit else ""
            lines.append(f"  {display_name}: {converted}{suffix}")
    return "\n".join(lines)


def format_prefetch_for_llm(source_key: str, raw_json: str) -> str:
    """
    Convert raw akshare JSON (from _filter_by_period) into a compact,
    LLM-friendly text for Phase-1 parsing.

    Falls back to the original JSON string on any parse error so the
    pipeline never hard-fails.
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return raw_json  # fallback: send as-is

    if not isinstance(data, list) or not data:
        return raw_json

    key = source_key.lower()

    if "income_statement_quarterly" in key or "income_statement_report" in key:
        note = ("【注意】这是单季度差分数据" if "quarterly" in key
                else "【注意】这是累计年度数据")
        body = _format_records(data, _INCOME_STMT_FIELDS)
        return f"来源：{source_key} — 利润表\n{note}\n{body}"

    if "balance_sheet" in key:
        body = _format_records(data, _BALANCE_SHEET_FIELDS)
        return f"来源：{source_key} — 资产负债表（期末余额）\n{body}"

    if "cashflow_quarterly" in key or "cashflow_report" in key:
        note = ("【注意】这是单季度数据" if "quarterly" in key else "【注意】这是累计年度数据")
        body = _format_records(data, _CASHFLOW_FIELDS)
        return (
            f"来源：{source_key} — 现金流量表\n{note}\n"
            "【重要】NETCASH_OPERATE（经营活动现金流量净额）是总量（亿元），"
            "与 PER_NETCASH（每股经营现金流，元/股）是不同概念，请勿混淆。\n"
            f"{body}"
        )

    if "financial_indicators_em_quarterly" in key:
        body = _format_records(data, _EM_QUARTERLY_FIELDS)
        return (
            f"来源：{source_key} — 东方财富单季度财务指标\n"
            "【重要】PER_ 开头字段均为每股指标（元/股），总量字段已标注[总量]，"
            "勿将每股值当作总量（亿元）处理。\n"
            f"{body}"
        )

    if "financial_indicators_em_by_report" in key:
        body = _format_records(data, _EM_BY_REPORT_FIELDS)
        return (
            f"来源：{source_key} — 东方财富按报告期财务指标（累计）\n"
            "【重要】PER_ 开头字段均为每股指标（元/股），总量字段已标注[总量]，"
            "勿将每股值当作总量（亿元）处理。\n"
            f"{body}"
        )

    if "main_business" in key:
        lines = [f"来源：{source_key} — 主营业务构成（全年累计，非单季度）"]
        lines.append("【注意】此数据为全年累计，period 应标注为年度（如2025年）而非单季度。")
        for rec in data:
            raw_date = rec.get("报告日期", "")
            # akshare returns Unix-ms timestamps for this interface
            date_val = _ts_to_date(raw_date) if isinstance(raw_date, (int, float)) else str(raw_date)[:10]
            lines.append(f"\n【报告期：{date_val}】")
            for raw_key, (display_name, converter, unit) in _MAIN_BUSINESS_FIELDS.items():
                raw_val = rec.get(raw_key)
                if raw_val is None or str(raw_val) in ("nan", "None"):
                    continue
                converted = converter(raw_val) if converter is not str else str(raw_val)
                if converted is None:
                    continue
                suffix = f" {unit}" if unit else ""
                lines.append(f"  {display_name}: {converted}{suffix}")
        return "\n".join(lines)

    # Unknown interface — return as-is
    return raw_json
