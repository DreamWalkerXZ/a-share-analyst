import json
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
    "get_financial_indicators_em",
    "get_main_business_breakdown",
]

MAX_TOOL_CALLS = 30
# For quarterly / snapshot interfaces, keep the target period + 1 prior period for YoY context.
# For annual / cumulative interfaces (income_report, indicators_by_report) keep only the target year.
PREFETCH_MAX_RECORDS = 2     # quarterly interfaces: 2 records (target + comparison)
PREFETCH_MAX_RECORDS_ANNUAL = 1  # annual interfaces: 1 record (target year only)
PREFETCH_MAX_CHARS = 20_000  # hard cap per interface to protect LLM context

# Annual interfaces return cumulative year-to-date data; only the target year is meaningful.
_ANNUAL_ACTIONS = {
    "get_income_statement_report",
    "get_financial_indicators_em_by_report",
}
TOOL_RESULT_PARSE_CHARS = 8_000  # chars of raw tool result fed to the inline parse LLM call

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
            return json_str[:PREFETCH_MAX_CHARS] if len(json_str) > PREFETCH_MAX_CHARS else json_str

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

        result = json.dumps(filtered[:max_records], ensure_ascii=False)
    except Exception:
        result = json_str

    return result[:PREFETCH_MAX_CHARS] + ("... [truncated]" if len(result) > PREFETCH_MAX_CHARS else "")


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
                    structured_data_tool._run(
                        action=action,
                        params={"symbol": f"{stock_code}.{prefix}", "indicator": "按报告期"},
                    ),
                    cutoff,
                    max_records=PREFETCH_MAX_RECORDS_ANNUAL,
                )
                results["get_financial_indicators_em_quarterly"] = _filter_by_period(
                    structured_data_tool._run(
                        action=action,
                        params={"symbol": f"{stock_code}.{prefix}", "indicator": "按单季度"},
                    ),
                    cutoff,
                    max_records=PREFETCH_MAX_RECORDS,
                )
            else:
                n = PREFETCH_MAX_RECORDS_ANNUAL if action in _ANNUAL_ACTIONS else PREFETCH_MAX_RECORDS
                results[action] = _filter_by_period(
                    structured_data_tool._run(
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


def _save_collected_data(company: str, period: str, collected: dict) -> None:
    """Persist collected_data to output/ as JSON for debugging."""
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
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
        return json.loads(content.strip())
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
                raw_result = tool.invoke(tool_args)
                if isinstance(raw_result, dict):
                    raw_result = json.dumps(raw_result, ensure_ascii=False)
        except Exception as exc:
            raw_result = f"ERROR: {exc}"

        raw_str = str(raw_result)

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


def _round_collected(collected: dict) -> dict:
    """Round numeric values in collected_data to 2 decimal places."""
    result = {}
    for key, entry in collected.items():
        if not isinstance(entry, dict):
            result[key] = entry
            continue
        value = entry.get("value")
        if isinstance(value, float):
            entry = {**entry, "value": round(value, 2)}
        result[key] = entry
    return result


def run_data_collection(company: str, stock_code: str, period: str) -> dict:
    """Orchestrate Phase 1 (pre-fetch + LLM parse) then Phase 2 (ReAct loop)."""
    cutoff = _period_to_cutoff(period)
    print(f"[data_collection] 阶段一：预取 {len(PREFETCH_ACTIONS)} 个核心接口（截止 {cutoff}）...")
    raw = prefetch_core_data(stock_code, period)

    print("[data_collection] 阶段一：LLM 解析原始数据...")
    initial_collected = _parse_prefetched(company, stock_code, period, raw)
    print(f"[data_collection] 阶段一完成，初始化 {len(initial_collected)} 条数据项")
    _save_collected_data(company, period, initial_collected)

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

    _save_collected_data(company, period, final_collected)
    return final_collected
