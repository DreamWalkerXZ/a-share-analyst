import json
import signal
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from openai import BadRequestError, InternalServerError

from src.agent.state import DataCollectionState
from src.prompts.data_collection import PHASE1_PARSE_PROMPT, PHASE2_PARSE_PROMPT, PHASE2_SYSTEM_PROMPT
from src.utils.compact import compact_collected
from src.utils.llm import get_llm
from src.utils.prefetch_formatter import format_prefetch_for_llm
from src.tools.calculator import FinancialCalculatorTool
from src.tools.search import RealTimeSearchTool
from src.tools.structured_data import StructuredDataTool

structured_data_tool = StructuredDataTool()
search_tool = RealTimeSearchTool()
calculator_tool = FinancialCalculatorTool()

TOOLS = [structured_data_tool, search_tool, calculator_tool]
TOOL_MAP = {t.name: t for t in TOOLS}

PREFETCH_ACTIONS = [
    "get_income_statement_quarterly",
    "get_income_statement_report",
    "get_balance_sheet_report",
    "get_cashflow_quarterly",
    "get_cashflow_report",
    "get_financial_indicators_em",
    "get_main_business_breakdown",
    # High-probability Phase 2 calls — always needed for sections 2-3
    "get_peer_valuation",
    "get_peer_dupont",
    "get_profit_forecast_eps",
    "get_profit_forecast_net_profit",
    "get_dividend_history_cninfo",
]

MAX_TOOL_CALLS = 30
# For quarterly / snapshot interfaces, keep the target period + 1 prior period for YoY context.
# For annual / cumulative interfaces (income_report, indicators_by_report) keep only the target year.
PREFETCH_MAX_RECORDS = 2     # quarterly interfaces: 2 records (target + comparison)
PREFETCH_MAX_RECORDS_ANNUAL = 1  # annual interfaces: 1 record (target year only)
PREFETCH_MAX_CHARS = 20_000  # hard cap per interface to protect LLM context
PREFETCH_TIMEOUT_SECONDS = 45  # akshare sources can hang indefinitely
TOOL_TIMEOUT_SECONDS = 45      # bound Phase-2 tool calls for the same reason

# Annual interfaces return cumulative year-to-date data; only the target year is meaningful.
_ANNUAL_ACTIONS = {
    "get_income_statement_report",
    "get_cashflow_report",
    "get_financial_indicators_em_by_report",
}

# Actions using plain stock code (no exchange prefix) — mostly 同花顺 interfaces.
_PLAIN_CODE_ACTIONS = {
    "get_profit_forecast_eps",
    "get_profit_forecast_net_profit",
    "get_dividend_history_cninfo",
}

# Forecast/dividend data is forward-looking or historical; don't filter by period.
_NO_PERIOD_FILTER = {
    "get_peer_valuation",
    "get_peer_dupont",
    "get_profit_forecast_eps",
    "get_profit_forecast_net_profit",
    "get_dividend_history_cninfo",
}
TOOL_RESULT_PARSE_CHARS = 8_000  # chars of raw tool result fed to the inline parse LLM call
MAX_ENTRIES_PER_CALL = 12       # hard cap on new entries extracted per tool call

_QUARTER_END = {"Q1": "03-31", "Q2": "06-30", "Q3": "09-30", "Q4": "12-31"}


def _exchange_prefix(stock_code: str) -> str:
    return "SH" if stock_code.startswith("6") else "SZ"


def _period_to_cutoff(period: str) -> str:
    """Convert '2025Q4' → '2025-12-31'. Returns '9999-12-31' for empty/unknown input."""
    if len(period) == 6 and period[4] == "Q":
        year, q = period[:4], period[4:]
        return f"{year}-{_QUARTER_END.get(q, '12-31')}"
    return "9999-12-31"


def _filter_by_period(json_str: str, cutoff: str, max_records: int = PREFETCH_MAX_RECORDS) -> str:
    """Keep records on or before cutoff (YYYY-MM-DD), sorted newest-first, capped at max_records."""
    try:
        records = json.loads(json_str)
        if not isinstance(records, list) or not records:
            return json_str

        # Auto-detect first YYYY-MM-DD field as the date key
        date_key = None
        for key, val in records[0].items():
            s = str(val or "")
            if len(s) >= 10 and s[4] == "-" and s[7] == "-":
                date_key = key
                break

        if date_key:
            filtered = [r for r in records if str(r.get(date_key, "9999-12-31"))[:10] <= cutoff]
            filtered.sort(key=lambda r: str(r.get(date_key, "")), reverse=True)
        else:
            filtered = records

        return json.dumps(filtered[:max_records], ensure_ascii=False)
    except Exception:
        return json_str


@contextmanager
def _prefetch_deadline(seconds: int):
    """Bound one pre-fetch call so a slow external data source cannot block the run."""
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _raise_timeout(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"pre-fetch timed out after {seconds}s")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def _run_prefetch_action(action: str, params: dict) -> str:
    with _prefetch_deadline(PREFETCH_TIMEOUT_SECONDS):
        return structured_data_tool._run(action=action, params=params)


def prefetch_core_data(stock_code: str, period: str = "") -> dict[str, str]:
    """Phase 1: Call mandatory interfaces; filter to period cutoff; return raw JSON keyed by action."""
    prefix = _exchange_prefix(stock_code)
    symbol_em = f"{prefix}{stock_code}"
    cutoff = _period_to_cutoff(period)
    results: dict[str, str] = {}

    for action in PREFETCH_ACTIONS:
        print(f"[data_collection] 阶段一：获取 {action}...")
        try:
            if action == "get_financial_indicators_em":
                results["get_financial_indicators_em_by_report"] = _filter_by_period(
                    _run_prefetch_action(
                        action=action,
                        params={"symbol": f"{stock_code}.{prefix}", "indicator": "按报告期"},
                    ),
                    cutoff,
                    max_records=PREFETCH_MAX_RECORDS_ANNUAL,
                )
                results["get_financial_indicators_em_quarterly"] = _filter_by_period(
                    _run_prefetch_action(
                        action=action,
                        params={"symbol": f"{stock_code}.{prefix}", "indicator": "按单季度"},
                    ),
                    cutoff,
                    max_records=PREFETCH_MAX_RECORDS,
                )
            elif action in _PLAIN_CODE_ACTIONS:
                raw = _run_prefetch_action(
                    action=action, params={"symbol": stock_code}
                )
                results[action] = raw[:PREFETCH_MAX_CHARS] + ("... [truncated]" if len(raw) > PREFETCH_MAX_CHARS else "")
            else:
                n = PREFETCH_MAX_RECORDS_ANNUAL if action in _ANNUAL_ACTIONS else PREFETCH_MAX_RECORDS
                results[action] = _filter_by_period(
                    _run_prefetch_action(
                        action=action, params={"symbol": symbol_em}
                    ),
                    cutoff,
                    max_records=n,
                )
        except Exception as exc:
            print(f"[data_collection] 阶段一：{action} 失败：{exc}")
            results[action] = f"ERROR: {exc}"

    return results


def _parse_one_source(
    llm, company: str, stock_code: str, period: str, source_key: str, source_data: str
) -> dict:
    """Parse a single data source into collected_data entries."""
    formatted = format_prefetch_for_llm(source_key, source_data)
    prompt = PHASE1_PARSE_PROMPT.format(
        company=company,
        stock_code=stock_code,
        period=period,
        raw_data=formatted,
    )
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        content: str = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif not content.strip().startswith("{"):
            return {}
        return json.loads(content.strip())
    except Exception as exc:
        print(f"[data_collection] 阶段一：解析 {source_key} 失败：{exc}")
        return {}


_SOURCE_PRIORITY: dict[str, int] = {
    # Lower number = higher priority; keeps the winning entry on duplicate (label, period).
    "get_income_statement_quarterly":      1,
    "get_income_statement_report":         2,
    "get_cashflow_quarterly":              1,
    "get_financial_indicators_em_quarterly": 1,
    "get_financial_indicators_em_by_report": 2,
    "get_balance_sheet_report":            1,
    "get_main_business_breakdown":         1,
}


def _dedup_collected(entries_by_source: list[tuple[str, dict]]) -> dict:
    """
    Merge per-source dicts, resolving (label, period) collisions by source priority.
    Entries from higher-priority sources (lower score) overwrite lower-priority ones.
    """
    # seen[(label, period)] = (priority, key)
    seen: dict[tuple, tuple[int, str]] = {}
    merged: dict = {}

    for source_key, entries in entries_by_source:
        priority = _SOURCE_PRIORITY.get(source_key, 99)
        for key, val in entries.items():
            if not isinstance(val, dict):
                merged[key] = val
                continue
            sig = (val.get("label", key), val.get("period", ""))
            existing_priority, existing_key = seen.get(sig, (999, ""))
            if priority < existing_priority:
                # Remove lower-priority duplicate
                if existing_key and existing_key in merged:
                    del merged[existing_key]
                merged[key] = val
                seen[sig] = (priority, key)
            elif priority == existing_priority and key not in merged:
                merged[key] = val
                seen[sig] = (priority, key)
            # else: existing has higher priority, skip
    return merged


def _parse_prefetched(company: str, stock_code: str, period: str, raw: dict[str, str]) -> dict:
    """Ask LLM to parse each pre-fetched data source; merge with deduplication."""
    llm = get_llm()
    entries_by_source: list[tuple[str, dict]] = []
    for source_key, source_data in raw.items():
        if source_data.startswith("ERROR:"):
            continue
        print(f"[data_collection] 阶段一：解析 {source_key}...")
        entries = _parse_one_source(llm, company, stock_code, period, source_key, source_data)
        entries_by_source.append((source_key, entries))
    return _dedup_collected(entries_by_source)


def _save_collected_data(company: str, period: str, collected: dict, phase: str = "", output_dir: str = "", prefix: str = "") -> None:
    """Persist collected_data as JSON. phase='1' or '2' for eval naming."""
    out_dir = Path(output_dir) if output_dir else Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    if prefix:
        suffix = f"-Phase{phase}.json" if phase else "_collected_data.json"
        path = out_dir / f"{prefix}{suffix}"
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"{company}_{period}_{ts}_collected_data.json"
    path.write_text(json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[data_collection] 已保存 collected_data → {path}")



def _build_system_message(company: str, stock_code: str, period: str, collected_data: dict) -> SystemMessage:
    """Build PHASE2_SYSTEM_PROMPT with pre-computed symbol formats and compact collected data."""
    prefix = _exchange_prefix(stock_code)
    symbol_em = f"{prefix}{stock_code}"          # e.g. SH600519
    symbol_em_dot = f"{stock_code}.{prefix}"     # e.g. 600519.SH
    symbol_plain = stock_code                    # e.g. 600519
    existing_data = compact_collected(collected_data) or "（暂无）"
    return SystemMessage(content=PHASE2_SYSTEM_PROMPT.format(
        company=company,
        stock_code=stock_code,
        period=period,
        symbol_em=symbol_em,
        symbol_em_dot=symbol_em_dot,
        symbol_plain=symbol_plain,
        existing_count=len(collected_data),
        existing_data=existing_data,
    ))


def _parse_tool_result(
    llm,
    company: str,
    stock_code: str,
    period: str,
    tool_name: str,
    tool_args: dict,
    raw: str,
    collected_data: dict,
) -> dict:
    """Parse a Phase-2 tool result using context-aware PHASE2_PARSE_PROMPT."""
    if raw.startswith("ERROR:") or len(raw) < 20:
        return {}

    tool_args_str = json.dumps(tool_args, ensure_ascii=False)
    # Brief version for use inside the KEY / source field
    tool_args_brief = tool_args_str[:120] + ("..." if len(tool_args_str) > 120 else "")

    prompt = PHASE2_PARSE_PROMPT.format(
        company=company,
        stock_code=stock_code,
        period=period,
        tool_name=tool_name,
        tool_args=tool_args_str,
        tool_args_brief=tool_args_brief,
        raw_data=raw[:TOOL_RESULT_PARSE_CHARS],
        existing_count=len(collected_data),
        existing_summary=compact_collected(collected_data) or "（暂无）",
    )
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        content: str = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif not content.strip().startswith("{"):
            return {}
        parsed = json.loads(content.strip())
        # Filter out entries where value is None (LLM couldn't extract a concrete value)
        filtered = {
            k: v for k, v in parsed.items()
            if isinstance(v, dict) and v.get("value") is not None
        }
        # Reject qualitative/fake-number entries
        _REJECT_UNITS = {"次", "定性"}
        filtered = {k: v for k, v in filtered.items() if v.get("unit") not in _REJECT_UNITS}
        # Hard cap: LLM is asked to rank by relevance; keep the first MAX_ENTRIES_PER_CALL
        if len(filtered) > MAX_ENTRIES_PER_CALL:
            filtered = dict(list(filtered.items())[:MAX_ENTRIES_PER_CALL])
            print(f"[data_collection] 阶段二：解析结果截断至 {MAX_ENTRIES_PER_CALL} 条")
        return filtered
    except Exception as exc:
        print(f"[data_collection] 阶段二：解析 {tool_name} 结果失败：{exc}")
        return {}


def _format_parsed_summary(entries: dict) -> str:
    """Return a compact human-readable summary of newly parsed collected_data entries."""
    if not entries:
        return "[解析完成] 未提取到有效数据条目"
    lines = [f"[解析完成] 新增 {len(entries)} 条数据："]
    for key, val in entries.items():
        if isinstance(val, dict):
            label = val.get("label", "")
            value = val.get("value", "")
            unit = val.get("unit", "")
            lines.append(f"  · {key}: {label} = {value} {unit}".rstrip())
    return "\n".join(lines)


def react_reason(state: DataCollectionState) -> DataCollectionState:
    """Phase 2: LLM decides the next tool call or signals DONE.

    The system message is kept unchanged across iterations to maximise prompt
    cache hit rate. The agent learns what has already been collected via the
    compact '[解析完成]' ToolMessage summaries in the conversation history.
    """
    llm = get_llm().bind_tools(TOOLS)
    try:
        response = llm.invoke(state["messages"])
    except (BadRequestError, InternalServerError) as exc:
        # Covers two cases:
        # 1. BadRequestError: model does not support tool-calling (e.g. GLM-4.5-air).
        # 2. InternalServerError: some providers wrap context-length exceeded as HTTP 500.
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        print(
            f"[data_collection] 阶段二：LLM 请求失败（错误码 {code}：{exc}），"
            "提前结束 ReAct 阶段。"
        )
        return {**state, "messages": state["messages"] + [AIMessage(content="DONE")]}

    return {**state, "messages": state["messages"] + [response]}


def react_tool(state: DataCollectionState) -> DataCollectionState:
    """Execute tool calls, immediately parse results into collected_data, store compact summary."""
    last_msg = state["messages"][-1]
    new_messages = list(state["messages"])
    new_collected = dict(state["collected_data"])
    tool_call_count = state["tool_call_count"]
    llm = get_llm()

    for tool_call in last_msg.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool_call_count += 1
        print(
            f"[data_collection] 阶段二：调用 {tool_name}"
            f"（轮次 {tool_call_count}/{MAX_TOOL_CALLS}）"
        )

        try:
            tool = TOOL_MAP.get(tool_name)
            if tool is None:
                raw_result = f"ERROR: 未知工具 {tool_name}"
            else:
                with _prefetch_deadline(TOOL_TIMEOUT_SECONDS):
                    raw_result = tool.invoke(tool_args)
                if isinstance(raw_result, dict):
                    raw_result = json.dumps(raw_result, ensure_ascii=False)
        except Exception as exc:
            raw_result = f"ERROR: {exc}"

        raw_str = str(raw_result)

        if raw_str.startswith("ERROR:"):
            # Propagate errors directly so the agent can fix its call (e.g. add missing params).
            summary = f"[工具调用失败] {raw_str[:400]}"
        else:
            # Immediately parse raw result → update collected_data → store compact summary.
            entries = _parse_tool_result(
                llm,
                state["company"],
                state["stock_code"],
                state["period"],
                tool_name,
                tool_args,
                raw_str,
                new_collected,  # pass snapshot so LLM can deduplicate
            )
            new_collected.update(entries)
            summary = _format_parsed_summary(entries)

        new_messages.append(ToolMessage(content=summary, tool_call_id=tool_call["id"]))

    return {
        **state,
        "messages": new_messages,
        "collected_data": new_collected,
        "tool_call_count": tool_call_count,
    }


def _should_continue(state: DataCollectionState) -> Literal["react_tool", "__end__"]:
    if state["tool_call_count"] >= MAX_TOOL_CALLS:
        print(f"[data_collection] 达到最大调用轮次 {MAX_TOOL_CALLS}，停止收集")
        return "__end__"
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "react_tool"
    return "__end__"


def build_data_collection_subgraph():
    graph = StateGraph(DataCollectionState)
    graph.add_node("react_reason", react_reason)
    graph.add_node("react_tool", react_tool)
    graph.set_entry_point("react_reason")
    graph.add_conditional_edges("react_reason", _should_continue)
    graph.add_edge("react_tool", "react_reason")
    return graph.compile()


def _prior_period(period: str) -> str | None:
    """Return the prior-year period string, or None if unparseable.
    Examples: '2025Q4' -> '2024Q4', '2025年' -> '2024年'
    """
    import re
    m = re.match(r"^(\d{4})(Q\d|年)$", period)
    if not m:
        return None
    return f"{int(m.group(1)) - 1}{m.group(2)}"


def _find_value(collected: dict, company: str, period: str, label: str) -> float | None:
    """Find a numeric value in collected_data by company, period, and label."""
    for key, entry in collected.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("period") == period and entry.get("label") == label:
            v = entry.get("value")
            if isinstance(v, (int, float)):
                return float(v)
    return None


def auto_derive_metrics(company: str, collected: dict) -> dict:
    """Compute derived metrics from collected_data using pure Python arithmetic."""
    derived: dict = {}
    periods = sorted({e["period"] for e in collected.values() if isinstance(e, dict) and e.get("period")})

    # --- 2a. Expense ratios ---
    expense_items = [
        ("营业税金及附加", "税金及附加率"),
        ("销售费用", "销售费用率"),
        ("管理费用", "管理费用率"),
    ]
    for period in periods:
        revenue = _find_value(collected, company, period, "营业总收入")
        if revenue is None or revenue == 0:
            continue
        for expense_label, ratio_label in expense_items:
            expense = _find_value(collected, company, period, expense_label)
            if expense is None:
                continue
            ratio = round(expense / revenue * 100, 2)
            derived[f"{company}_{period}_{ratio_label}"] = {
                "label": ratio_label,
                "value": ratio,
                "unit": "%",
                "period": period,
                "source": "auto_derived",
                "raw_field": "",
                "notes": f"{expense_label} / 营业总收入",
            }

    # --- 2b. Dividend payout ratio ---
    # Dividend data from cninfo has value = per-10-shares amount (e.g. 276.73 = "10派276.73元").
    # Formula: payout_ratio = sum(div_per_10 / 10) / EPS * 100
    annual_periods = [p for p in periods if p.endswith("年")]
    for period in annual_periods:
        eps = _find_value(collected, company, period, "基本每股收益（EPS）")
        if eps is None:
            eps = _find_value(collected, company, period, "基本每股收益")
        if eps is None or eps <= 0:
            continue
        dividend_per_share_total = 0.0
        for key, entry in collected.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("unit") != "元/股":
                continue
            label = entry.get("label", "")
            if "分红" not in label:
                continue
            # Skip cash-flow entries (分红偿债支付的现金 is in 亿元, not 元/股 — already filtered above)
            entry_period = entry.get("period", "")
            if entry_period == period or entry_period.startswith(period[:4]):
                div_val = entry.get("value")
                if isinstance(div_val, (int, float)):
                    dividend_per_share_total += float(div_val) / 10.0
        if dividend_per_share_total > 0:
            payout = round(dividend_per_share_total / eps * 100, 2)
            derived[f"{company}_{period}_分红率"] = {
                "label": "分红率",
                "value": payout,
                "unit": "%",
                "period": period,
                "source": "auto_derived",
                "raw_field": "",
                "notes": "年度+中期派息合计（每10股）/ 基本每股收益",
            }

    # --- 2c. Margin pct-point changes ---
    # Note: requires prior-year data. With PREFETCH_MAX_RECORDS_ANNUAL=1, only
    # the target year's data is available, so this typically produces no results
    # for annual periods. Quarterly periods benefit from PREFETCH_MAX_RECORDS=2.
    margin_labels = [
        "销售毛利率", "销售净利率", "酒类毛利率",
    ]
    # Also include any auto-derived expense ratios for pct-point change
    margin_labels += [r for _, r in expense_items]
    for period in periods:
        prior = _prior_period(period)
        if prior is None:
            continue
        for label in margin_labels:
            current = _find_value(collected, company, period, label)
            prior_val = _find_value(collected, company, prior, label)
            if current is None or prior_val is None:
                continue
            change = round(current - prior_val, 2)
            derived[f"{company}_{period}_{label}_变动"] = {
                "label": f"{label}变动",
                "value": change,
                "unit": "pct",
                "period": period,
                "source": "auto_derived",
                "raw_field": "",
                "notes": f"{period} vs {prior}",
            }

    print(f"[data_collection] 自动计算完成，新增 {len(derived)} 条衍生指标")
    return derived


_RATIO_LABELS = {"占比", "比例", "份额", "比重", "渗透率"}


def _round_collected(collected: dict) -> dict:
    """Round numeric values and normalize percentage-like ratios to %."""
    result = {}
    for key, entry in collected.items():
        if not isinstance(entry, dict):
            result[key] = entry
            continue
        value = entry.get("value")
        label = entry.get("label", "")
        unit = entry.get("unit", "")
        if isinstance(value, (int, float)) and 0 < value < 1 and not unit:
            if any(kw in label for kw in _RATIO_LABELS):
                entry = {**entry, "value": round(value * 100, 2), "unit": "%"}
        if isinstance(value, float):
            entry = {**entry, "value": round(entry["value"], 2)}
        result[key] = entry
    return result


def run_data_collection(company: str, stock_code: str, period: str, output_dir: str = "", prefix: str = "") -> dict:
    """Orchestrate Phase 1 (pre-fetch + LLM parse) then Phase 2 (ReAct loop)."""
    cutoff = _period_to_cutoff(period)
    print(f"[data_collection] 阶段一：预取 {len(PREFETCH_ACTIONS)} 个核心接口（截止 {cutoff}）...")
    raw = prefetch_core_data(stock_code, period)

    print("[data_collection] 阶段一：LLM 解析原始数据...")
    initial_collected = _parse_prefetched(company, stock_code, period, raw)
    print(f"[data_collection] 阶段一完成，初始化 {len(initial_collected)} 条数据项")

    print("[data_collection] 阶段一：自动计算衍生指标...")
    derived = auto_derive_metrics(company, initial_collected)
    initial_collected.update(derived)

    _save_collected_data(company, period, initial_collected, phase="1", output_dir=output_dir, prefix=prefix)

    initial_state: DataCollectionState = {
        "messages": [_build_system_message(company, stock_code, period, initial_collected)],
        "collected_data": initial_collected,
        "tool_call_count": 0,
        "company": company,
        "stock_code": stock_code,
        "period": period,
    }

    subgraph = build_data_collection_subgraph()
    final_state = subgraph.invoke(initial_state)

    # Tool results are parsed inline during react_tool; no consolidation pass needed.
    final_collected = _round_collected(dict(final_state["collected_data"]))
    print(f"[data_collection] 阶段二完成，共 {len(final_collected)} 条数据项")

    _save_collected_data(company, period, final_collected, phase="2", output_dir=output_dir, prefix=prefix)
    return final_collected
