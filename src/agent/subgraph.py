import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from openai import BadRequestError

from src.agent.state import DataCollectionState
from src.prompts.data_collection import PHASE1_PARSE_PROMPT, PHASE2_SYSTEM_PROMPT
from src.utils.llm import get_llm
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
PREFETCH_MAX_RECORDS = 8    # keep N most-recent records per interface (on or before cutoff)
PREFETCH_MAX_CHARS = 20_000  # hard cap per interface to protect LLM context

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
                )
                results["get_financial_indicators_em_quarterly"] = _filter_by_period(
                    structured_data_tool._run(
                        action=action,
                        params={"symbol": f"{stock_code}.{prefix}", "indicator": "按单季度"},
                    ),
                    cutoff,
                )
            else:
                results[action] = _filter_by_period(
                    structured_data_tool._run(
                        action=action, params={"symbol": symbol_em}
                    ),
                    cutoff,
                )
        except Exception as exc:
            print(f"[data_collection] 阶段一：{action} 失败：{exc}")
            results[action] = f"ERROR: {exc}"

    return results


def _parse_one_source(
    llm, company: str, stock_code: str, period: str, source_key: str, source_data: str
) -> dict:
    """Parse a single data source into collected_data entries."""
    prompt = PHASE1_PARSE_PROMPT.format(
        company=company,
        stock_code=stock_code,
        period=period,
        raw_data=json.dumps({source_key: source_data}, ensure_ascii=False),
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


def _parse_prefetched(company: str, stock_code: str, period: str, raw: dict[str, str]) -> dict:
    """Ask LLM to parse each pre-fetched data source individually; merge results."""
    llm = get_llm()
    collected: dict = {}
    for source_key, source_data in raw.items():
        if source_data.startswith("ERROR:"):
            continue
        print(f"[data_collection] 阶段一：解析 {source_key}...")
        entries = _parse_one_source(llm, company, stock_code, period, source_key, source_data)
        collected.update(entries)
    return collected


def _save_collected_data(company: str, period: str, collected: dict) -> None:
    """Persist collected_data to output/ as JSON for debugging."""
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{company}_{period}_{ts}_collected_data.json"
    path.write_text(json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[data_collection] 已保存 collected_data → {path}")


def _extract_collected_data_from_message(content: str) -> dict:
    """Extract any JSON blocks from a message and return as dict (best-effort)."""
    result = {}
    for block in content.split("```json"):
        if "```" in block:
            json_str = block.split("```")[0].strip()
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, dict):
                    result.update(parsed)
            except json.JSONDecodeError:
                pass
    return result


def react_reason(state: DataCollectionState) -> DataCollectionState:
    """Phase 2: LLM decides next tool call or signals DONE; also parses prior tool results."""
    llm = get_llm().bind_tools(TOOLS)
    try:
        response = llm.invoke(state["messages"])
    except BadRequestError as exc:
        # Some models (e.g. GLM-4.5-air) do not support tool-calling.
        # Gracefully skip Phase 2 rather than crashing the pipeline.
        print(
            f"[data_collection] 阶段二：模型不支持 tool calling（错误码 {exc.code}），"
            "跳过 ReAct 阶段。如需补充数据请换用支持 function calling 的模型"
            "（如 glm-4-flash / glm-4-plus / gpt-4o）。"
        )
        return {
            **state,
            "messages": state["messages"] + [AIMessage(content="DONE")],
        }

    # Merge any collected_data entries the LLM included in its text
    new_collected = dict(state["collected_data"])
    if isinstance(response.content, str):
        extracted = _extract_collected_data_from_message(response.content)
        new_collected.update(extracted)

    return {
        **state,
        "messages": state["messages"] + [response],
        "collected_data": new_collected,
    }


def react_tool(state: DataCollectionState) -> DataCollectionState:
    """Execute tool calls from the last AI message and append raw results to messages."""
    last_msg = state["messages"][-1]
    new_messages = list(state["messages"])
    tool_call_count = state["tool_call_count"]

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

        new_messages.append(
            ToolMessage(content=str(raw_result), tool_call_id=tool_call["id"])
        )

    return {**state, "messages": new_messages, "tool_call_count": tool_call_count}


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


def run_data_collection(company: str, stock_code: str, period: str) -> dict:
    """Orchestrate Phase 1 (pre-fetch + LLM parse) then Phase 2 (ReAct loop)."""
    cutoff = _period_to_cutoff(period)
    print(f"[data_collection] 阶段一：预取 {len(PREFETCH_ACTIONS)} 个核心接口（截止 {cutoff}）...")
    raw = prefetch_core_data(stock_code, period)

    print("[data_collection] 阶段一：LLM 解析原始数据...")
    initial_collected = _parse_prefetched(company, stock_code, period, raw)
    print(f"[data_collection] 阶段一完成，初始化 {len(initial_collected)} 条数据项")
    _save_collected_data(company, period, initial_collected)

    system_prompt = PHASE2_SYSTEM_PROMPT.format(
        company=company,
        stock_code=stock_code,
        period=period,
        existing_keys="\n".join(f"- {k}" for k in initial_collected),
    )
    initial_state: DataCollectionState = {
        "messages": [SystemMessage(content=system_prompt)],
        "collected_data": initial_collected,
        "tool_call_count": 0,
    }

    subgraph = build_data_collection_subgraph()
    final_state = subgraph.invoke(initial_state)

    final_collected = final_state["collected_data"]
    print(f"[data_collection] 数据收集完成，共 {len(final_collected)} 条数据项")
    return final_collected
