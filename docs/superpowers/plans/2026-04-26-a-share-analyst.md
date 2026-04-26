# A股研报生成器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a LangGraph-based CLI tool that takes a company name and reporting period, collects financial data via akshare + search, and generates a structured Markdown research report using LLM.

**Architecture:** Three-node LangGraph pipeline: `data_collection` (two-phase: pre-fetch 6 core interfaces → LLM parses → ReAct free loop), `report_generation` (chapters 1→2→3→4→0, each with generate + validate + one retry), `output` (write `.md` file). Three tools: `StructuredDataTool` (akshare + jina), `RealTimeSearchTool` (Serper), `FinancialCalculatorTool`. All tool outputs are raw data; LLM is responsible for parsing results into the `collected_data` schema.

**Tech Stack:** Python 3.11+, uv, langgraph, langchain-openai, akshare, requests, python-dotenv, pytest, pytest-mock

---

## File Map

| File | Responsibility |
|------|---------------|
| `main.py` | CLI entry: `parse_input()` + graph launch |
| `src/agent/state.py` | `ReportState` and `DataCollectionState` TypedDicts |
| `src/utils/stock_code.py` | `data/stock_code_map.json` cache, `lookup_stock_code()` |
| `src/tools/structured_data.py` | `StructuredDataTool` + `INTERFACE_MAP` for all akshare calls |
| `src/tools/search.py` | `RealTimeSearchTool` via Serper API |
| `src/tools/calculator.py` | `FinancialCalculatorTool` with sandboxed eval |
| `src/prompts/data_collection.py` | Phase 1 parse prompt + Phase 2 system prompt |
| `src/prompts/report_sections.py` | Per-chapter generation prompts + validation prompt |
| `src/agent/subgraph.py` | `prefetch_core_data()`, LLM parse phase, `react_reason` / `react_tool` nodes, `build_data_collection_subgraph()`, `run_data_collection()` |
| `src/agent/nodes.py` | `data_collection_node`, `report_generation_node`, `output_node`, `generate_and_validate_section()`, `assemble_report()` |
| `src/agent/graph.py` | Main `StateGraph` wiring |

---

### Task 1: Project setup

**Files:**
- Modify: `pyproject.toml`
- Create: `.env.example`
- Create: `src/__init__.py`, `src/agent/__init__.py`, `src/tools/__init__.py`, `src/prompts/__init__.py`, `src/utils/__init__.py`
- Create: `data/.gitkeep`, `output/.gitkeep`, `tests/__init__.py`

- [ ] **Step 1: Add runtime and dev dependencies**

```bash
uv add akshare langchain langchain-openai langgraph langchain-community requests python-dotenv
uv add --dev pytest pytest-mock
```

Expected: `pyproject.toml` updated, `uv.lock` created.

- [ ] **Step 2: Create package directories**

```bash
mkdir -p src/agent src/tools src/prompts src/utils data output tests
touch src/__init__.py src/agent/__init__.py src/tools/__init__.py \
      src/prompts/__init__.py src/utils/__init__.py \
      data/.gitkeep output/.gitkeep tests/__init__.py
```

- [ ] **Step 3: Create `.env.example`**

```
OPENAI_API_KEY=your-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o
SERPER_API_KEY=your-key-here
```

- [ ] **Step 4: Verify imports**

```bash
uv run python -c "import langchain; import langgraph; import akshare; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add .
git commit -m "chore: set up project dependencies and directory structure"
```

---

### Task 2: State definitions

**Files:**
- Create: `src/agent/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state.py
from src.agent.state import ReportState, DataCollectionState


def test_report_state_keys():
    state: ReportState = {
        "company": "贵州茅台",
        "stock_code": "600519",
        "period": "2025Q4",
        "collected_data": {},
        "sections": {},
        "output_path": "",
    }
    assert state["company"] == "贵州茅台"
    assert state["period"] == "2025Q4"


def test_data_collection_state_keys():
    state: DataCollectionState = {
        "messages": [],
        "collected_data": {},
        "tool_call_count": 0,
    }
    assert state["tool_call_count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_state.py -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

```python
# src/agent/state.py
from typing import TypedDict

from langchain_core.messages import BaseMessage


class ReportState(TypedDict):
    company: str
    stock_code: str
    period: str
    collected_data: dict
    sections: dict
    output_path: str


class DataCollectionState(TypedDict):
    messages: list[BaseMessage]
    collected_data: dict
    tool_call_count: int
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_state.py -v
```

Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agent/state.py tests/test_state.py
git commit -m "feat: add ReportState and DataCollectionState TypedDicts"
```

---

### Task 3: Stock code cache utility

**Files:**
- Create: `src/utils/stock_code.py`
- Create: `tests/test_stock_code.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stock_code.py
import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.utils.stock_code import lookup_stock_code


def test_lookup_existing_code(tmp_path, monkeypatch):
    cache = {"贵州茅台": "600519", "_updated_at": "2026-04-26"}
    cache_file = tmp_path / "stock_code_map.json"
    cache_file.write_text(json.dumps(cache, ensure_ascii=False))
    monkeypatch.setattr("src.utils.stock_code.CACHE_PATH", cache_file)
    assert lookup_stock_code("贵州茅台") == "600519"


def test_lookup_missing_raises(tmp_path, monkeypatch):
    cache = {"招商银行": "600036", "_updated_at": "2026-04-26"}
    cache_file = tmp_path / "stock_code_map.json"
    cache_file.write_text(json.dumps(cache, ensure_ascii=False))
    monkeypatch.setattr("src.utils.stock_code.CACHE_PATH", cache_file)
    with pytest.raises(ValueError, match="找不到股票代码"):
        lookup_stock_code("不存在公司")


def test_cache_auto_created_when_missing(tmp_path, monkeypatch):
    cache_file = tmp_path / "stock_code_map.json"
    monkeypatch.setattr("src.utils.stock_code.CACHE_PATH", cache_file)
    mock_df = pd.DataFrame({"name": ["贵州茅台", "招商银行"], "code": ["600519", "600036"]})
    with patch("akshare.stock_info_a_code_name", return_value=mock_df):
        result = lookup_stock_code("贵州茅台")
    assert result == "600519"
    assert cache_file.exists()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_stock_code.py -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

```python
# src/utils/stock_code.py
import json
from datetime import date, datetime
from pathlib import Path

import akshare as ak

CACHE_PATH = Path("data/stock_code_map.json")
CACHE_TTL_DAYS = 30


def _refresh_cache() -> dict[str, str]:
    df = ak.stock_info_a_code_name()
    mapping: dict[str, str] = dict(zip(df["name"], df["code"]))
    mapping["_updated_at"] = date.today().isoformat()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2))
    return mapping


def _load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        data = json.loads(CACHE_PATH.read_text())
        updated_at = datetime.fromisoformat(data.get("_updated_at", "2000-01-01")).date()
        if (date.today() - updated_at).days <= CACHE_TTL_DAYS:
            return data
    return _refresh_cache()


def lookup_stock_code(company_name: str) -> str:
    mapping = _load_cache()
    code = mapping.get(company_name)
    if not code:
        raise ValueError(
            f"找不到股票代码：{company_name}。"
            "请使用完整公司名，或直接传入股票代码（如 '600519 2025 Q4'）"
        )
    return code
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_stock_code.py -v
```

Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/utils/stock_code.py tests/test_stock_code.py
git commit -m "feat: add stock code cache utility with auto-refresh"
```

---

### Task 4: CLI `parse_input`

**Files:**
- Modify: `main.py`
- Create: `tests/test_main.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_main.py
import pytest
from unittest.mock import patch

from main import parse_input


def test_parse_company_name_and_period():
    with patch("main.lookup_stock_code", return_value="600519"):
        result = parse_input("贵州茅台 2025 Q4")
    assert result == {"company": "贵州茅台", "stock_code": "600519", "period": "2025Q4"}


def test_parse_numeric_stock_code_directly():
    result = parse_input("600519 2025 Q4")
    assert result == {"company": "600519", "stock_code": "600519", "period": "2025Q4"}


def test_parse_q1_period():
    with patch("main.lookup_stock_code", return_value="600036"):
        result = parse_input("招商银行 2025 Q1")
    assert result["period"] == "2025Q1"


def test_invalid_format_raises():
    with pytest.raises(ValueError, match="输入格式错误"):
        parse_input("贵州茅台")


def test_invalid_quarter_raises():
    with patch("main.lookup_stock_code", return_value="600519"):
        with pytest.raises(ValueError, match="无效季度"):
            parse_input("贵州茅台 2025 Q5")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_main.py -v
```

Expected: FAIL with `ImportError` or `AttributeError`

- [ ] **Step 3: Implement**

```python
# main.py
import re
import sys

from dotenv import load_dotenv

from src.utils.stock_code import lookup_stock_code

load_dotenv()

VALID_QUARTERS = {"Q1", "Q2", "Q3", "Q4"}


def parse_input(raw: str) -> dict:
    """Parse CLI input like "贵州茅台 2025 Q4" into {company, stock_code, period}.

    Also accepts "600519 2025 Q4" when the first token is a 6-digit stock code.
    """
    parts = raw.strip().split()
    if len(parts) < 3:
        raise ValueError(f"输入格式错误，期望：'公司名 年份 季度'，实际收到：{raw!r}")

    quarter = parts[-1].upper()
    year = parts[-2]
    company = " ".join(parts[:-2])

    if quarter not in VALID_QUARTERS:
        raise ValueError(f"无效季度 {quarter!r}，应为 Q1/Q2/Q3/Q4")

    period = f"{year}{quarter}"

    if re.fullmatch(r"\d{6}", company):
        stock_code = company
    else:
        stock_code = lookup_stock_code(company)

    return {"company": company, "stock_code": stock_code, "period": period}


def main():
    if len(sys.argv) < 2:
        print('用法：uv run main.py "公司名 年份 季度"')
        print('示例：uv run main.py "贵州茅台 2025 Q4"')
        sys.exit(1)

    params = parse_input(sys.argv[1])
    print(f"[main] 解析完成：{params}")

    from src.agent.graph import build_graph  # noqa: PLC0415 — lazy import avoids slow startup

    graph = build_graph()
    initial_state = {
        "company": params["company"],
        "stock_code": params["stock_code"],
        "period": params["period"],
        "collected_data": {},
        "sections": {},
        "output_path": "",
    }
    final_state = graph.invoke(initial_state)
    print(f"\n研报生成完成：{final_state['output_path']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_main.py -v
```

Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: add CLI parse_input with period and stock code resolution"
```

---

### Task 5: `StructuredDataTool`

**Files:**
- Create: `src/tools/structured_data.py`
- Create: `tests/test_structured_data.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_structured_data.py
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_structured_data.py -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

```python
# src/tools/structured_data.py
from typing import Any

import requests
import akshare as ak
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

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


class StructuredDataInput(BaseModel):
    action: str = Field(description="接口名称，如 get_income_statement_quarterly")
    params: dict = Field(default_factory=dict, description="接口参数，如 {'symbol': 'SH600519'}")


class StructuredDataTool(BaseTool):
    name: str = "structured_data"
    description: str = (
        "从 akshare 获取结构化金融数据（返回原始 JSON），"
        "或通过 fetch_url_as_markdown 将网页/PDF 转为 Markdown。"
        "action 为接口名称，params 为接口参数。"
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

        df = INTERFACE_MAP[action](params)
        return df.to_json(orient="records", force_ascii=False, date_format="iso")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_structured_data.py -v
```

Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/tools/structured_data.py tests/test_structured_data.py
git commit -m "feat: add StructuredDataTool with full akshare interface mapping"
```

---

### Task 6: `RealTimeSearchTool`

**Files:**
- Create: `src/tools/search.py`
- Create: `tests/test_search.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_search.py
import os
import pytest
from unittest.mock import MagicMock, patch

from src.tools.search import RealTimeSearchTool


def test_search_returns_formatted_results(mocker):
    tool = RealTimeSearchTool()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "organic": [
            {"title": "茅台年报点评", "snippet": "净利润同比增长12%", "link": "https://a.com"},
            {"title": "茅台估值分析", "snippet": "PE约28倍低于历史均值", "link": "https://b.com"},
        ]
    }
    mock_resp.raise_for_status = MagicMock()
    mocker.patch("requests.post", return_value=mock_resp)
    with patch.dict(os.environ, {"SERPER_API_KEY": "test-key"}):
        result = tool._run(query="贵州茅台2025年报分析")
    assert "茅台年报点评" in result
    assert "净利润同比增长12%" in result


def test_search_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    tool = RealTimeSearchTool()
    with pytest.raises(ValueError, match="SERPER_API_KEY"):
        tool._run(query="test")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_search.py -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

```python
# src/tools/search.py
import os

import requests
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

SERPER_URL = "https://google.serper.dev/search"


class SearchInput(BaseModel):
    query: str = Field(description="搜索查询词")


class RealTimeSearchTool(BaseTool):
    name: str = "realtime_search"
    description: str = (
        "使用 Serper 搜索引擎获取实时信息。"
        "用于 akshare 无法覆盖的行业数据、分析师预期、可比公司估值等。"
    )
    args_schema: type[BaseModel] = SearchInput

    def _run(self, query: str) -> str:  # type: ignore[override]
        api_key = os.environ.get("SERPER_API_KEY")
        if not api_key:
            raise ValueError("SERPER_API_KEY 环境变量未设置")

        resp = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": 5},
            timeout=15,
        )
        resp.raise_for_status()

        results = []
        for item in resp.json().get("organic", []):
            results.append(
                f"**{item.get('title', '')}**\n"
                f"{item.get('snippet', '')}\n"
                f"URL: {item.get('link', '')}"
            )
        return "\n\n".join(results) if results else "无搜索结果"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_search.py -v
```

Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/tools/search.py tests/test_search.py
git commit -m "feat: add RealTimeSearchTool via Serper API"
```

---

### Task 7: `FinancialCalculatorTool`

**Files:**
- Create: `src/tools/calculator.py`
- Create: `tests/test_calculator.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_calculator.py
import math
import pytest

from src.tools.calculator import FinancialCalculatorTool


def test_yoy_growth_calculation():
    tool = FinancialCalculatorTool()
    result = tool._run(
        expression="(current - prev) / prev * 100",
        variables={"current": 423.58, "prev": 377.05},
        description="营收同比增速",
    )
    assert abs(result["result"] - 12.34) < 0.01
    assert "current" in result["steps"]
    assert result["description"] == "营收同比增速"


def test_math_functions_allowed():
    tool = FinancialCalculatorTool()
    result = tool._run(
        expression="math.sqrt(value)",
        variables={"value": 9.0},
        description="开方测试",
    )
    assert result["result"] == 3.0


def test_blocked_import_raises():
    tool = FinancialCalculatorTool()
    with pytest.raises(ValueError, match="不允许"):
        tool._run(
            expression="__import__('os').system('ls')",
            variables={},
            description="恶意代码",
        )


def test_division_by_zero_returns_none():
    tool = FinancialCalculatorTool()
    result = tool._run(
        expression="a / b",
        variables={"a": 100.0, "b": 0.0},
        description="除以零",
    )
    assert result["result"] is None
    assert "ZeroDivisionError" in result["steps"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_calculator.py -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

```python
# src/tools/calculator.py
import math

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

BLOCKED_KEYWORDS = [
    "__import__", "import", "exec", "eval", "open",
    "os", "sys", "subprocess", "builtins",
]


class CalculatorInput(BaseModel):
    expression: str = Field(description="Python 数学表达式，如 '(a - b) / b * 100'")
    variables: dict[str, float] = Field(description="变量名到数值的映射")
    description: str = Field(description="计算目的说明，如 '计算营收同比增速'")


class FinancialCalculatorTool(BaseTool):
    name: str = "financial_calculator"
    description: str = (
        "在受限沙箱中执行财务计算表达式（仅允许 math 模块和四则运算）。"
        "expression 为 Python 数学表达式，variables 为变量值映射。"
    )
    args_schema: type[BaseModel] = CalculatorInput

    def _run(  # type: ignore[override]
        self, expression: str, variables: dict[str, float], description: str
    ) -> dict:
        for keyword in BLOCKED_KEYWORDS:
            if keyword in expression:
                raise ValueError(f"表达式中不允许使用 '{keyword}'")

        steps = f"计算：{description}\n表达式：{expression}\n变量：{variables}"
        allowed_globals = {"__builtins__": {}, "math": math}

        try:
            result = eval(expression, allowed_globals, dict(variables))  # noqa: S307
            steps += f"\n结果：{result}"
            return {"result": float(result), "steps": steps, "description": description}
        except ZeroDivisionError:
            steps += "\n错误：ZeroDivisionError"
            return {"result": None, "steps": steps, "description": description}
        except Exception as exc:
            steps += f"\n错误：{exc}"
            return {"result": None, "steps": steps, "description": description}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_calculator.py -v
```

Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/tools/calculator.py tests/test_calculator.py
git commit -m "feat: add FinancialCalculatorTool with sandboxed eval"
```

---

### Task 8: Prompts

**Files:**
- Create: `src/prompts/data_collection.py`
- Create: `src/prompts/report_sections.py`

- [ ] **Step 1: Create `src/prompts/data_collection.py`**

```python
# src/prompts/data_collection.py

PHASE1_PARSE_PROMPT = """\
你是一位专业的金融数据分析师。以下是从 akshare 获取的原始财务数据（JSON 格式）。

公司：{company}（股票代码：{stock_code}）
报告期：{period}

原始数据：
{raw_data}

请将上述数据提炼为 collected_data 条目。每条条目格式：
{{
  "KEY": {{
    "label": "中文语义名称",
    "value": 数值或字符串,
    "unit": "单位（亿元/%/元/股，无则空字符串）",
    "period": "数据所属报告期，如 2025Q4",
    "source": "平台-数据集名称 函数名",
    "raw_field": "原始字段名",
    "notes": "可选说明（差分计算方式等）"
  }}
}}

KEY 格式："{company}_{period}_{指标名}"，如 "贵州茅台_2025Q4_归母净利润"

要求：
1. 重点提取：营业收入、净利润、归母净利润、毛利率、净利率、ROE、EPS、\
经营现金流净额、资产负债率等核心指标
2. 季度数据若需差分计算单季度值，在 notes 中说明
3. 金额统一换算为亿元（原始元 ÷1e8，原始万元 ÷1e4）
4. 仅输出 JSON，不加任何解释

输出：
```json
{{
  "KEY1": {{...}},
  "KEY2": {{...}}
}}
```
"""

PHASE2_SYSTEM_PROMPT = """\
你是一位专业的 A 股研究员，正在为 {company}（{stock_code}）{period} 收集研报数据。

已收集的数据键（勿重复获取）：
{existing_keys}

需要补充的数据类别（按优先级排序）：
1. 同行对比：get_peer_valuation, get_peer_dupont, get_peer_scale
2. 盈利预测：get_profit_forecast_eps, get_profit_forecast_net_profit, \
get_profit_forecast_institutions
3. 分红历史：get_dividend_history_cninfo
4. 估值快照：get_spot_valuation
5. 公告与研报：get_notices_individual（财务报告类）, get_research_reports
6. 行业数据：get_industry_pe 或 realtime_search 搜索行业 PE、景气度

工具使用规则：
- structured_data：调用 akshare 接口，返回原始 JSON
- realtime_search：搜索 akshare 无法覆盖的行业信息
- financial_calculator：计算增速、比率等衍生指标

每次工具调用后，将结果提炼为新的 collected_data 条目追加到回复中：
```json
{{"KEY": {{"label": ..., "value": ..., "unit": ..., "period": ..., "source": ..., \
"raw_field": ..., "notes": ...}}}}
```

数据足够时输出：DONE

股票代码格式说明：
- 东方财富接口 symbol 参数：SH600519（沪市）或 SZ000858（深市）
- 同花顺接口 symbol 参数：600519
- notices_individual 的 security 参数：600519
- financial_indicators_em 的 symbol 参数：600519.SH 或 000858.SZ
"""
```

- [ ] **Step 2: Create `src/prompts/report_sections.py`**

```python
# src/prompts/report_sections.py

SECTION_SYSTEM_PROMPT = """\
你是一位专业的 A 股卖方研究员，正在撰写 {company} {period} 季报点评研报。

研报标题格式：《公司名 + 触发事件 + 核心结论 + 投资评级》
示例：《贵州茅台2025年年报点评：主动出清积极求变，维持"买入"评级》

写作要求：
- 使用专业的证券研究语言
- 数据引用须与 collected_data 中的数值精确一致
- 避免套话，结论需有数据支撑
"""

SECTION_PROMPTS: dict[str, dict] = {
    "section_1": {
        "title": "业绩与经营情况",
        "data_categories": ["income_statement", "balance_sheet", "cashflow",
                            "financial_indicators", "main_business"],
        "prompt": """\
请撰写研报第一章：业绩与经营情况。

参考数据（collected_data 子集）：
{data_subset}

要求：
1. 营业收入、净利润、归母净利润的同比/环比变化及驱动因素
2. 毛利率、净利率变化分析（收入结构、成本）
3. 主营业务构成（产品/地区维度）
4. 经营现金流质量
5. ROE、ROA、资产负债率关键变化

输出格式：
```json
{{"content": "章节正文 Markdown 内容", "data_refs": ["引用的 collected_data 键名"]}}
```
""",
    },
    "section_2": {
        "title": "发展展望与投资逻辑",
        "data_categories": ["peer_comparison", "research_reports", "search_results", "industry"],
        "prompt": """\
请撰写研报第二章：发展展望与投资逻辑。

前序章节内容：
{prior_sections}

参考数据（collected_data 子集）：
{data_subset}

要求：
1. 行业景气度与趋势判断
2. 公司核心竞争力（品牌/渠道/技术/成本优势）
3. 未来增长驱动因素
4. 与可比公司的差异化优势

输出格式：
```json
{{"content": "章节正文 Markdown 内容", "data_refs": ["引用的 collected_data 键名"]}}
```
""",
    },
    "section_3": {
        "title": "盈利预测与估值",
        "data_categories": ["profit_forecast", "spot_valuation", "peer_valuation", "dividend"],
        "prompt": """\
请撰写研报第三章：盈利预测与估值。

前序章节内容：
{prior_sections}

参考数据（collected_data 子集）：
{data_subset}

要求：
1. 未来 2-3 年 EPS 和净利润预测（引用分析师一致预期）
2. PE/PB 估值（当前 vs 历史均值 vs 可比公司）
3. 目标价测算（给出具体目标价区间和方法论）
4. 投资评级及理由

输出格式：
```json
{{"content": "章节正文 Markdown 内容", "data_refs": ["引用的 collected_data 键名"]}}
```
""",
    },
    "section_4": {
        "title": "风险提示",
        "data_categories": ["all"],
        "prompt": """\
请撰写研报第四章：风险提示。

前序章节内容：
{prior_sections}

全部数据：
{data_subset}

要求：
1. 列出 3-5 个针对该公司和行业的具体风险
2. 每个风险说明触发条件和潜在影响程度
3. 避免"市场风险"、"政策风险"等无具体内容的套话

输出格式：
```json
{{"content": "章节正文 Markdown 内容", "data_refs": ["引用的 collected_data 键名"]}}
```
""",
    },
    "section_0": {
        "title": "开篇总览",
        "data_categories": ["all"],
        "prompt": """\
请撰写研报开篇总览（执行摘要）。

全部章节内容：
{prior_sections}

要求：
1. 核心业绩速览（3-5 个最关键数据，含同比增速）
2. 投资结论（1-2 句话概括核心观点）
3. 评级与目标价
4. 各章节一句话提要

输出格式：
```json
{{"content": "章节正文 Markdown 内容", "data_refs": ["引用的 collected_data 键名"]}}
```
""",
    },
}

VALIDATION_PROMPT = """\
你是一位严格的研报质检员。请检查以下研报章节的数据准确性。

章节内容：
{content}

可查阅的 collected_data（事实来源）：
{data_subset}

检查要点：
1. 章节中引用的每个具体数值是否可在 collected_data 中找到对应条目
2. 引用数值是否与 collected_data 中的 value 字段一致（允许正常单位换算）
3. 同比/环比增速计算是否正确

输出格式：
```json
{{"passed": true/false, "issues": ["具体问题（passed 为 true 时为空列表）"]}}
```
"""
```

- [ ] **Step 3: Verify imports**

```bash
uv run python -c "
from src.prompts.data_collection import PHASE1_PARSE_PROMPT, PHASE2_SYSTEM_PROMPT
from src.prompts.report_sections import SECTION_PROMPTS, VALIDATION_PROMPT
assert '{company}' in PHASE1_PARSE_PROMPT
assert len(SECTION_PROMPTS) == 5
print('OK')
"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/prompts/data_collection.py src/prompts/report_sections.py
git commit -m "feat: add data collection and report section prompts"
```

---

### Task 9: `data_collection` subgraph

**Files:**
- Create: `src/agent/subgraph.py`
- Create: `tests/test_subgraph.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_subgraph.py
from unittest.mock import MagicMock, patch

import pytest

from src.agent.subgraph import PREFETCH_ACTIONS, build_data_collection_subgraph, prefetch_core_data


def test_prefetch_actions_has_six_entries():
    assert len(PREFETCH_ACTIONS) == 6
    assert "get_income_statement_quarterly" in PREFETCH_ACTIONS
    assert "get_balance_sheet_report" in PREFETCH_ACTIONS
    assert "get_financial_indicators_em" in PREFETCH_ACTIONS


def test_prefetch_core_data_calls_all_actions(mocker):
    mock_tool = MagicMock()
    mock_tool._run.return_value = '[{"REPORT_DATE": "2025-12-31", "VALUE": 100}]'
    mocker.patch("src.agent.subgraph.structured_data_tool", mock_tool)
    results = prefetch_core_data(stock_code="600519")
    # financial_indicators_em is called twice (by_report + quarterly), rest once each
    assert mock_tool._run.call_count == len(PREFETCH_ACTIONS) + 1
    assert "get_income_statement_quarterly" in results
    assert "get_financial_indicators_em_by_report" in results
    assert "get_financial_indicators_em_quarterly" in results


def test_subgraph_is_compilable():
    graph = build_data_collection_subgraph()
    assert graph is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_subgraph.py -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

```python
# src/agent/subgraph.py
import json
import os
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from src.agent.state import DataCollectionState
from src.prompts.data_collection import PHASE1_PARSE_PROMPT, PHASE2_SYSTEM_PROMPT
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


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        base_url=os.environ.get("OPENAI_BASE_URL"),
        api_key=os.environ.get("OPENAI_API_KEY"),
    )


def _exchange_prefix(stock_code: str) -> str:
    return "SH" if stock_code.startswith("6") else "SZ"


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
                results["get_financial_indicators_em_by_report"] = structured_data_tool._run(
                    action=action,
                    params={"symbol": f"{stock_code}.{prefix}", "indicator": "按报告期"},
                )
                results["get_financial_indicators_em_quarterly"] = structured_data_tool._run(
                    action=action,
                    params={"symbol": f"{stock_code}.{prefix}", "indicator": "按单季度"},
                )
            else:
                results[action] = structured_data_tool._run(
                    action=action, params={"symbol": symbol_em}
                )
        except Exception as exc:
            print(f"[data_collection] 阶段一：{action} 失败：{exc}")
            results[action] = f"ERROR: {exc}"

    return results


def _parse_prefetched(company: str, stock_code: str, period: str, raw: dict[str, str]) -> dict:
    """Ask LLM to parse all pre-fetched raw data into collected_data entries in one call."""
    llm = _get_llm()
    prompt = PHASE1_PARSE_PROMPT.format(
        company=company,
        stock_code=stock_code,
        period=period,
        raw_data=json.dumps(raw, ensure_ascii=False, indent=2),
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    content: str = response.content

    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif content.strip().startswith("{"):
        content = content.strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        print("[data_collection] 阶段一：LLM 解析 JSON 失败，返回空 collected_data")
        return {}


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
    llm = _get_llm().bind_tools(TOOLS)
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_subgraph.py -v
```

Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agent/subgraph.py tests/test_subgraph.py
git commit -m "feat: add data_collection two-phase subgraph"
```

---

### Task 10: `report_generation` and `output` nodes

**Files:**
- Create: `src/agent/nodes.py`
- Create: `tests/test_nodes.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_nodes.py
import json
from unittest.mock import MagicMock

from src.agent.nodes import assemble_report, generate_and_validate_section

MOCK_DATA = {
    "贵州茅台_2025Q4_营收": {
        "label": "营业收入",
        "value": 423.58,
        "unit": "亿元",
        "period": "2025Q4",
        "source": "东方财富",
        "raw_field": "TOTAL_OPERATE_INCOME",
        "notes": "",
    }
}


def _make_llm(side_effects: list) -> MagicMock:
    llm = MagicMock()
    llm.invoke.side_effect = [MagicMock(content=c) for c in side_effects]
    return llm


def test_generate_section_pass_on_first_attempt(mocker):
    gen_json = json.dumps({"content": "## 业绩\n\n2025Q4营收423.58亿元", "data_refs": []})
    val_json = json.dumps({"passed": True, "issues": []})
    mocker.patch("src.agent.nodes._get_llm", return_value=_make_llm([gen_json, val_json]))

    result = generate_and_validate_section(
        section_key="section_1",
        company="贵州茅台",
        period="2025Q4",
        collected_data=MOCK_DATA,
        prior_sections={},
    )
    assert "业绩" in result
    assert "⚠️" not in result


def test_generate_section_marks_warning_after_two_failures(mocker):
    gen_json = json.dumps({"content": "## 业绩\n\n数据有误", "data_refs": []})
    val_fail = json.dumps({"passed": False, "issues": ["数值与来源不符"]})
    mocker.patch(
        "src.agent.nodes._get_llm",
        return_value=_make_llm([gen_json, val_fail, gen_json, val_fail]),
    )

    result = generate_and_validate_section(
        section_key="section_1",
        company="贵州茅台",
        period="2025Q4",
        collected_data=MOCK_DATA,
        prior_sections={},
    )
    assert "⚠️ 需要人工验证" in result


def test_assemble_report_orders_sections_correctly():
    sections = {
        "section_0": "## 开篇总览\n内容",
        "section_1": "## 业绩\n内容",
        "section_2": "## 展望\n内容",
        "section_3": "## 估值\n内容",
        "section_4": "## 风险\n内容",
    }
    report = assemble_report("贵州茅台", "2025Q4", sections)
    assert report.index("## 开篇总览") < report.index("## 业绩")
    assert report.index("## 业绩") < report.index("## 风险")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_nodes.py -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

```python
# src/agent/nodes.py
import json
import os
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from src.agent.state import ReportState
from src.agent.subgraph import run_data_collection
from src.prompts.report_sections import SECTION_PROMPTS, SECTION_SYSTEM_PROMPT, VALIDATION_PROMPT


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        base_url=os.environ.get("OPENAI_BASE_URL"),
        api_key=os.environ.get("OPENAI_API_KEY"),
    )


def _filter_data(collected_data: dict, categories: list[str]) -> dict:
    if "all" in categories:
        return collected_data

    keyword_map = {
        "income_statement": ["营收", "收入", "利润", "净利", "毛利", "成本"],
        "balance_sheet":    ["资产", "负债", "权益", "现金", "存货"],
        "cashflow":         ["现金流", "经营", "投资", "筹资"],
        "financial_indicators": ["ROE", "ROA", "EPS", "增速", "率"],
        "main_business":    ["主营", "业务", "产品", "地区"],
        "peer_comparison":  ["同行", "对比", "可比", "同业"],
        "research_reports": ["研报", "分析师", "评级"],
        "search_results":   ["搜索"],
        "industry":         ["行业", "PE", "景气"],
        "profit_forecast":  ["预测", "预期", "EPS预"],
        "spot_valuation":   ["估值", "市值", "PE", "PB"],
        "peer_valuation":   ["同行估值"],
        "dividend":         ["分红", "股息"],
    }

    relevant: set[str] = set()
    for cat in categories:
        kws = keyword_map.get(cat, [cat])
        for key, entry in collected_data.items():
            label = entry.get("label", "") if isinstance(entry, dict) else ""
            if any(kw in key or kw in label for kw in kws):
                relevant.add(key)

    return {k: collected_data[k] for k in relevant} if relevant else collected_data


def _parse_section_response(content: str) -> tuple[str, list[str]]:
    json_str = content
    if "```json" in content:
        json_str = content.split("```json")[1].split("```")[0].strip()
    elif not content.strip().startswith("{"):
        return content, []
    try:
        parsed = json.loads(json_str)
        return parsed.get("content", content), parsed.get("data_refs", [])
    except json.JSONDecodeError:
        return content, []


def _parse_validation_response(content: str) -> tuple[bool, list[str]]:
    json_str = content
    if "```json" in content:
        json_str = content.split("```json")[1].split("```")[0].strip()
    elif not content.strip().startswith("{"):
        return True, []
    try:
        parsed = json.loads(json_str)
        return parsed.get("passed", True), parsed.get("issues", [])
    except json.JSONDecodeError:
        return True, []


def generate_and_validate_section(
    section_key: str,
    company: str,
    period: str,
    collected_data: dict,
    prior_sections: dict,
) -> str:
    """Generate one report section with validation and a single retry on failure."""
    llm = _get_llm()
    spec = SECTION_PROMPTS[section_key]
    data_subset = _filter_data(collected_data, spec["data_categories"])
    data_json = json.dumps(data_subset, ensure_ascii=False, indent=2)
    prior_text = "\n\n".join(
        f"### {SECTION_PROMPTS[k]['title']}\n{v}" for k, v in prior_sections.items()
    )
    system = SECTION_SYSTEM_PROMPT.format(company=company, period=period)

    def _generate(extra: str = "") -> tuple[str, list[str]]:
        user = spec["prompt"].format(data_subset=data_json, prior_sections=prior_text)
        if extra:
            user += f"\n\n修正要求：{extra}"
        resp = llm.invoke([HumanMessage(content=system), HumanMessage(content=user)])
        return _parse_section_response(resp.content)

    def _validate(content: str) -> tuple[bool, list[str]]:
        prompt = VALIDATION_PROMPT.format(content=content, data_subset=data_json)
        resp = llm.invoke([HumanMessage(content=prompt)])
        return _parse_validation_response(resp.content)

    title = spec["title"]
    content, _ = _generate()
    passed, issues = _validate(content)

    if passed:
        print(f"[report_generation] {title} 验证通过")
        return content

    print(f"[report_generation] {title} 验证失败，正在重试...")
    retry_content, _ = _generate(extra="; ".join(issues))
    retry_passed, retry_issues = _validate(retry_content)

    if retry_passed:
        print(f"[report_generation] {title} 重试通过")
        return retry_content

    print(f"[report_generation] {title} 重试仍失败，标记人工验证")
    return retry_content + "\n\n⚠️ 需要人工验证：" + "; ".join(retry_issues)


def assemble_report(company: str, period: str, sections: dict) -> str:
    order = ["section_0", "section_1", "section_2", "section_3", "section_4"]
    parts = [f"# {company} {period} 季报点评\n"]
    for key in order:
        if key in sections:
            parts.append(sections[key])
    return "\n\n---\n\n".join(parts)


# ── LangGraph node functions ──────────────────────────────────────────────────

def data_collection_node(state: ReportState) -> ReportState:
    collected_data = run_data_collection(
        company=state["company"],
        stock_code=state["stock_code"],
        period=state["period"],
    )
    return {**state, "collected_data": collected_data}


def report_generation_node(state: ReportState) -> ReportState:
    company, period = state["company"], state["period"]
    collected_data = state["collected_data"]
    sections: dict[str, str] = {}

    chapter_order = ["section_1", "section_2", "section_3", "section_4", "section_0"]
    for i, key in enumerate(chapter_order, 1):
        title = SECTION_PROMPTS[key]["title"]
        print(f"[report_generation] 生成章节 {i}/{len(chapter_order)}：{title}")
        sections[key] = generate_and_validate_section(
            section_key=key,
            company=company,
            period=period,
            collected_data=collected_data,
            prior_sections=sections,
        )

    return {**state, "sections": sections}


def output_node(state: ReportState) -> ReportState:
    report_md = assemble_report(state["company"], state["period"], state["sections"])
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{state['company']}_{state['period']}_{ts}.md"
    path.write_text(report_md, encoding="utf-8")
    print(f"[output] 研报已保存至：{path}")
    return {**state, "output_path": str(path)}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_nodes.py -v
```

Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agent/nodes.py tests/test_nodes.py
git commit -m "feat: add report_generation and output nodes with validation retry"
```

---

### Task 11: Main graph

**Files:**
- Create: `src/agent/graph.py`
- Create: `tests/test_graph.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_graph.py
from src.agent.graph import build_graph


def test_graph_compilable():
    graph = build_graph()
    assert graph is not None


def test_graph_has_expected_nodes():
    graph = build_graph()
    node_names = list(graph.get_graph().nodes.keys())
    assert "data_collection" in node_names
    assert "report_generation" in node_names
    assert "output" in node_names
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_graph.py -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

```python
# src/agent/graph.py
from langgraph.graph import END, StateGraph

from src.agent.nodes import data_collection_node, output_node, report_generation_node
from src.agent.state import ReportState


def build_graph():
    graph = StateGraph(ReportState)
    graph.add_node("data_collection", data_collection_node)
    graph.add_node("report_generation", report_generation_node)
    graph.add_node("output", output_node)
    graph.set_entry_point("data_collection")
    graph.add_edge("data_collection", "report_generation")
    graph.add_edge("report_generation", "output")
    graph.add_edge("output", END)
    return graph.compile()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_graph.py -v
```

Expected: PASS (2 passed)

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Smoke-test the imports**

```bash
uv run python -c "
from main import parse_input
from unittest.mock import patch
with patch('main.lookup_stock_code', return_value='600519'):
    r = parse_input('贵州茅台 2025 Q4')
print('parse_input:', r)
from src.agent.graph import build_graph
g = build_graph()
print('build_graph: OK')
"
```

Expected:
```
parse_input: {'company': '贵州茅台', 'stock_code': '600519', 'period': '2025Q4'}
build_graph: OK
```

- [ ] **Step 7: Commit**

```bash
git add src/agent/graph.py tests/test_graph.py
git commit -m "feat: add main LangGraph StateGraph and wire all nodes"
```
