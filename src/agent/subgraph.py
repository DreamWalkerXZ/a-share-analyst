import json
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

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


def _exchange_prefix(stock_code: str) -> str:
    return "SH" if stock_code.startswith("6") else "SZ"


PREFETCH_MAX_RECORDS = 4   # keep only the most recent N rows per interface
PREFETCH_MAX_CHARS = 20000  # hard cap per interface to protect LLM context


def _truncate_json_records(json_str: str, n: int = PREFETCH_MAX_RECORDS) -> str:
    """Limit JSON array to first n records (most recent) and cap total character length."""
    try:
        records = json.loads(json_str)
        if isinstance(records, list) and len(records) > n:
            json_str = json.dumps(records[:n], ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        pass
    if len(json_str) > PREFETCH_MAX_CHARS:
        json_str = json_str[:PREFETCH_MAX_CHARS] + "... [truncated]"
    return json_str


def prefetch_core_data(stock_code: str) -> dict[str, str]:
    """Phase 1: Call mandatory interfaces directly; return raw JSON strings keyed by action."""
    prefix = _exchange_prefix(stock_code)
    symbol_em = f"{prefix}{stock_code}"
    results: dict[str, str] = {}

    for action in PREFETCH_ACTIONS:
        print(f"[data_collection] 阶段一：获取 {action}...")
        try:
            if action == "get_financial_indicators_em":
                # Called twice: by-report and quarterly
                results["get_financial_indicators_em_by_report"] = _truncate_json_records(
                    structured_data_tool._run(
                        action=action,
                        params={"symbol": f"{stock_code}.{prefix}", "indicator": "按报告期"},
                    )
                )
                results["get_financial_indicators_em_quarterly"] = _truncate_json_records(
                    structured_data_tool._run(
                        action=action,
                        params={"symbol": f"{stock_code}.{prefix}", "indicator": "按单季度"},
                    )
                )
            else:
                results[action] = _truncate_json_records(
                    structured_data_tool._run(
                        action=action, params={"symbol": symbol_em}
                    )
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
    response = llm.invoke(state["messages"])

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
    print(f"[data_collection] 阶段一：预取 {len(PREFETCH_ACTIONS)} 个核心接口...")
    raw = prefetch_core_data(stock_code)

    print("[data_collection] 阶段一：LLM 解析原始数据...")
    initial_collected = _parse_prefetched(company, stock_code, period, raw)
    print(f"[data_collection] 阶段一完成，初始化 {len(initial_collected)} 条数据项")

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
