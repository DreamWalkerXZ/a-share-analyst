# A股研报生成器 — 设计文档

**日期**：2026-04-26
**状态**：已确认

---

## 1. 目标与范围

基于 LangGraph 构建一个 A 股单季度个股研报生成 Agent，输入公司名称和报告期（如"贵州茅台 2025 Q4"），自动收集数据、逐章生成研报、LLM 验证，最终输出标准格式的 Markdown 研报文件。

**不在范围内**：Web UI、多股票批量生成、历史研报对比。

---

## 2. 运行方式

```bash
uv run main.py "贵州茅台 2025 Q4"
```

- 终端：只打印进度日志（当前节点、当前章节、工具调用情况）
- 输出：研报保存至 `output/<公司名>_<报告期>_<YYYYMMDD_HHMMSS>.md`

---

## 3. 整体架构

```
CLI: uv run main.py "贵州茅台 2025 Q4"
  └─ parse_input()
       ├─ 报告期解析：Q1→0331, Q2→0630, Q3→0930, Q4→1231
       └─ 公司名 → 股票代码：读 data/stock_code_map.json
            （首次运行或缓存超 30 天自动从 akshare 拉取并刷新）
            ↓
       { company, stock_code, period }

LangGraph 主图
┌──────────────────────────────────────────────────────────┐
│  [1] data_collection                                     │
│      自定义 ReAct 子图，三个工具，迭代收集数据             │
│      → 输出 collected_data: dict                         │
│                                                          │
│  [2] report_generation（章节顺序: 1→2→3→4→0）            │
│      每章：generate → validate → (retry once) → write    │
│      → 输出 sections: dict[str, str]                     │
│                                                          │
│  [3] output                                              │
│      拼装完整研报 → 写入 .md 文件                         │
└──────────────────────────────────────────────────────────┘
```

### LangGraph State

```python
class ReportState(TypedDict):
    company: str        # 公司名，如 "贵州茅台"
    stock_code: str     # 股票代码，如 "600519"
    period: str         # 报告期，如 "2025Q4"
    collected_data: dict  # 所有收集到的数据（见第 5 节）
    sections: dict        # 已生成章节，key 为 "section_0"~"section_4"
    output_path: str      # 输出文件路径
```

---

## 4. 公司名称 → 股票代码解析

本地缓存文件：`data/stock_code_map.json`

```json
{
  "贵州茅台": "600519",
  "招商银行": "600036",
  "_updated_at": "2026-04-26"
}
```

- 首次运行时调用 `ak.stock_info_a_code_name()` 写入缓存
- 缓存超过 30 天自动刷新
- 查不到时报错并提示用户直接输入股票代码

---

## 5. 三个工具

### 5.1 StructuredDataTool

**输入**：`{ action: str, params: dict }`

执行流程：查接口映射表 → 调 akshare → DataFrame → 转 dict → 包装为 `collected_data` 条目格式。

#### 接口映射（对应 `docs/akshare_interfaces/` 已验证 JSON 文件）

**核心财务数据**


| action                           | akshare 函数                              | 关键入参                               | 来源文件                                        |
| -------------------------------- | --------------------------------------- | ---------------------------------- | ------------------------------------------- |
| `get_balance_sheet_report`       | `stock_balance_sheet_by_report_em`      | `symbol="SH600519"`                | balance_sheet_report_period.json            |
| `get_income_statement_report`    | `stock_profit_sheet_by_report_em`       | `symbol="SH600519"`                | income_statement_report_period.json         |
| `get_income_statement_quarterly` | `stock_profit_sheet_by_quarterly_em`    | `symbol="SH600519"`                | income_statement_quarterly.json             |
| `get_cashflow_report`            | `stock_cash_flow_sheet_by_report_em`    | `symbol="SH600519"`                | cashflow_report_period.json                 |
| `get_cashflow_quarterly`         | `stock_cash_flow_sheet_by_quarterly_em` | `symbol="SH600519"`                | cashflow_quarterly.json                     |
| `get_balance_sheet_sina`         | `stock_financial_report_sina`           | `stock="sh600519", symbol="资产负债表"` | financial_statement_sina_balance_sheet.json |


**财务指标**


| action                          | akshare 函数                              | 关键入参                                  | 来源文件                           |
| ------------------------------- | --------------------------------------- | ------------------------------------- | ------------------------------ |
| `get_financial_indicators_em`   | `stock_financial_analysis_indicator_em` | `symbol="600519.SH", indicator="按报告期" | "按单季度"`                        |
| `get_financial_indicators_sina` | `stock_financial_analysis_indicator`    | `symbol="600519", start_year="2020"`  | financial_indicators_sina.json |


**业务结构**


| action                        | akshare 函数       | 关键入参                | 来源文件                           |
| ----------------------------- | ---------------- | ------------------- | ------------------------------ |
| `get_main_business_breakdown` | `stock_zygc_em`  | `symbol="SH600519"` | main_business_breakdown.json   |
| `get_main_business_profile`   | `stock_zyjs_ths` | `symbol="600519"`   | main_business_profile_ths.json |


**同行对比**


| action               | akshare 函数                         | 关键入参                | 来源文件                              |
| -------------------- | ---------------------------------- | ------------------- | --------------------------------- |
| `get_peer_valuation` | `stock_zh_valuation_comparison_em` | `symbol="SH600519"` | peer_valuation_comparison_em.json |
| `get_peer_dupont`    | `stock_zh_dupont_comparison_em`    | `symbol="SH600519"` | peer_dupont_comparison_em.json    |
| `get_peer_scale`     | `stock_zh_scale_comparison_em`     | `symbol="SH600519"` | peer_scale_comparison_em.json     |


**估值与分红**


| action                        | akshare 函数                      | 关键入参                              | 来源文件                            |
| ----------------------------- | ------------------------------- | --------------------------------- | ------------------------------- |
| `get_spot_valuation`          | `stock_individual_spot_xq`      | `symbol="SH600519"`               | spot_valuation_snapshot_xq.json |
| `get_dividend_history_cninfo` | `stock_dividend_cninfo`         | `symbol="600519"`                 | dividend_history_cninfo.json    |
| `get_dividend_history_sina`   | `stock_history_dividend_detail` | `symbol="600519", indicator="分红"` | dividend_history_sina.json      |


**盈利预测**


| action                             | akshare 函数                  | indicator 值       | 来源文件                                      |
| ---------------------------------- | --------------------------- | ----------------- | ----------------------------------------- |
| `get_profit_forecast_eps`          | `stock_profit_forecast_ths` | `"预测年报每股收益"`      | profit_forecast_eps_ths.json              |
| `get_profit_forecast_net_profit`   | `stock_profit_forecast_ths` | `"预测年报净利润"`       | profit_forecast_net_profit_ths.json       |
| `get_profit_forecast_institutions` | `stock_profit_forecast_ths` | `"业绩预测详表-机构"`     | profit_forecast_institutions_ths.json     |
| `get_profit_forecast_detailed`     | `stock_profit_forecast_ths` | `"业绩预测详表-详细指标预测"` | profit_forecast_detailed_metrics_ths.json |


**公告与研报**


| action                   | akshare 函数                       | 关键入参                                                                           | 来源文件                                 |
| ------------------------ | -------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------ |
| `get_notices_individual` | `stock_individual_notice_report` | `security="600519", symbol="财务报告", begin_date="YYYYMMDD", end_date="YYYYMMDD"` | notices_stock_financial_reports.json |
| `get_research_reports`   | `stock_research_report_em`       | `symbol="600519"`                                                              | research_reports_em.json             |


**行业与风险（可选）**


| action                  | akshare 函数                       | 关键入参                               | 来源文件                            |
| ----------------------- | -------------------------------- | ---------------------------------- | ------------------------------- |
| `get_industry_pe`       | `stock_industry_pe_ratio_cninfo` | `symbol="国证行业分类", date="YYYYMMDD"` | industry_pe_ratio_cninfo.json   |
| `get_industry_goodwill` | `stock_sy_hy_em`                 | `date="YYYYMMDD"`                  | industry_goodwill_em.json       |
| `get_pledge_ratio`      | `stock_gpzy_pledge_ratio_em`     | `date="YYYYMMDD"`                  | pledge_ratio_by_company_em.json |


**情绪指标（可选）**


| action                        | akshare 函数                           | 关键入参              | 来源文件                                            |
| ----------------------------- | ------------------------------------ | ----------------- | ----------------------------------------------- |
| `get_market_comment_overview` | `stock_comment_em`                   | 无                 | market_comment_overview_em.json                 |
| `get_comment_rating`          | `stock_comment_detail_zhpj_lspf_em`  | `symbol="600519"` | stock_comment_comprehensive_rating_em.json      |
| `get_comment_institution`     | `stock_comment_detail_zlkp_jgcyd_em` | `symbol="600519"` | stock_comment_institution_participation_em.json |


**网页/链接转 Markdown**


| action                  | 说明                                              | 关键入参       |
| ----------------------- | ----------------------------------------------- | ---------- |
| `fetch_url_as_markdown` | 在原始 URL 前拼接 `https://r.jina.ai/` 获取 Markdown 内容 | `url: str` |


**返回格式**：StructuredDataTool 返回**原始数据**，不做语义包装：akshare 接口返回 DataFrame 转换后的 JSON（保留原始列名和行数据）；`fetch_url_as_markdown` 返回 Markdown 字符串。由 LLM 负责将工具返回结果解析、提炼为 `collected_data` 条目格式（见第 6 节）。

---

### 5.2 RealTimeSearchTool

**输入**：`{ query: str }`

调用 Serper API，返回搜索结果摘要（标题 + snippet + url）。仅在 StructuredDataTool 无法覆盖时使用（行业数据、可比公司估值、分析师预期等）。

**环境变量**：`SERPER_API_KEY`

---

### 5.3 FinancialCalculatorTool

**输入**：`{ expression: str, variables: dict, description: str }`

- `expression`：Python 表达式字符串，如 `"(revenue_current - revenue_prev) / revenue_prev * 100"`
- `variables`：变量名到数值的映射
- `description`：计算目的说明

在受限沙箱中执行（仅允许 `math` 模块和基本运算）。

**返回**：`{ result: float, steps: str, description: str }`

同样返回原始结果，由 LLM 解析为 `collected_data` 条目，`notes` 字段记录变量代入过程。

---

## 6. collected_data 数据结构

`collected_data` 由 LLM 负责填写：工具返回原始数据，LLM 从中提炼关键字段、赋予语义标签后写入。每条数据为自解释结构，后续章节生成的 LLM 无需额外上下文即可理解：

```python
{
  "贵州茅台_2025Q4_归母净利润": {
    "label": "归母净利润（单季度）",
    "value": 176.93,
    "unit": "亿元",
    "period": "2025Q4",
    "source": "东方财富-利润表(按报告期) stock_profit_sheet_by_report_em",
    "raw_field": "PARENT_NETPROFIT",
    "notes": "单季度数据由报告期累计值差分计算得出"
  }
}
```

字段说明：

- `label`：中文语义名称
- `value`：数值、字符串或列表
- `unit`：单位（亿元、%、元/股 等）
- `period`：数据所属报告期
- `source`：`平台-数据集名称 函数名` 格式
- `raw_field`：原始字段名（便于溯源）
- `notes`：可选，说明计算方式或特殊处理

---

## 7. 数据收集节点（data_collection）

### 两阶段流程

```
阶段一：预取核心数据（固定、无 LLM 参与）
  │
  ├─ 直接调用必选接口列表（见下方），获取原始 DataFrame
  │   get_income_statement_quarterly
  │   get_income_statement_report
  │   get_balance_sheet_report
  │   get_cashflow_quarterly
  │   get_financial_indicators_em（按报告期 + 按单季度）
  │   get_main_business_breakdown
  │
  └─ 将全部原始结果拼接后交给 LLM 一次性解析
       → LLM 输出初始 collected_data 条目（填入 DataCollectionState）

阶段二：ReAct 自由补充（LLM 驱动）
  │
  react_reason →（有工具调用）→ react_tool → react_reason
               →（输出 DONE 或 tool_call_count ≥ 30）→ END
```

### 子图状态

```python
class DataCollectionState(TypedDict):
    messages: list[BaseMessage]   # ReAct 对话历史（含阶段一解析结果）
    collected_data: dict          # 累积收集的数据（阶段一后已有初始条目）
    tool_call_count: int          # 阶段二已调用轮次
```

### 阶段二节点说明

- `react_reason`：调用 LLM，持有三个工具的 schema，输出：
  1. 新的工具调用指令（如需补充数据）
  2. 对上一轮工具返回结果的解析 → 追加到 `collected_data`
  3. 或 DONE 信号（数据已足够）
- `react_tool`：执行工具调用，将原始结果追加到 `messages`，打印进度日志

### System Prompt 策略

- **阶段一解析 prompt**：给 LLM 全部原始数据 + 指令"提炼为 collected_data 条目格式，每条须有 label/value/unit/period/source/raw_field"
- **阶段二 system prompt**：给 LLM 一份补充采购清单，列出六大类数据中尚未覆盖的推荐接口（行业对比、盈利预测与估值、同行比较、公告与研报等），LLM 按需调用，缺失的走 RealTimeSearchTool 补充

### 进度日志示例

```
[data_collection] 阶段一：预取 6 个核心接口...
[data_collection] 阶段一：LLM 解析完成，初始化 28 条数据项
[data_collection] 阶段二：调用 get_peer_valuation（轮次 1/30）
[data_collection] 阶段二：调用 get_profit_forecast_eps（轮次 2/30）
[data_collection] 数据收集完成，共 47 条数据项
```

---

## 8. 章节生成与验证节点（report_generation）

章节生成顺序：**1 → 2 → 3 → 4 → 0**（章节 0 最后生成，依赖其他章节内容）

### 每章状态机

```
generate_section(chapter_n)
  │
  ├─ prompt = system_prompt
  │         + 前序章节内容
  │         + collected_data 子集（按章节预定义数据类别筛选）
  │         + 本章写作要求
  │
  ▼
LLM → { content: str, data_refs: list[str] }
  │
  ▼
validate_section
  LLM 检查：数据引用是否存在、数值是否与原始数据一致
  │
  ├─ PASS → 写入 sections["section_n"]
  │
  └─ FAIL → 带验证意见重新调用生成 LLM（一次）
               ├─ PASS → 写入 sections["section_n"]
               └─ FAIL → 写入 sections["section_n"]，末尾追加
                          ⚠️ 需要人工验证：<验证 LLM 给出的原因>
```

`report_generation` 为主图单节点，五章顺序执行。

### 进度日志示例

```
[report_generation] 生成章节 1/5：业绩与经营情况
[report_generation] 章节 1 验证通过
[report_generation] 生成章节 2/5：发展展望与投资逻辑
[report_generation] 章节 2 验证失败，正在重试...
[report_generation] 章节 2 重试通过
```

### 章节规范


| 章节  | 标题        | 核心内容                   | 依赖数据类别         |
| --- | --------- | ---------------------- | -------------- |
| 0   | 开篇总览      | 核心业绩速览 + 投资结论 + 评级     | 全部章节内容         |
| 1   | 业绩与经营情况   | 收入/成本/利润/现金流拆解         | 财务三表、财务指标、主营构成 |
| 2   | 发展展望与投资逻辑 | 行业趋势、竞争力、增长看点          | 行业对比、研究报告、搜索补充 |
| 3   | 盈利预测与估值   | EPS/净利润预测、PE/PB 估值、目标价 | 盈利预测、估值快照、同行估值 |
| 4   | 风险提示      | 针对性风险（非套话）             | 全部数据           |


### 研报标题格式

`公司名称 + 触发事件 + 核心结论 + 投资评级`

示例：《贵州茅台2025年年报点评：主动出清积极求变，维持"买入"评级》

---

## 9. 输出节点（output）

1. 拼装完整研报：标题 + 章节 0 + 章节 1 + 章节 2 + 章节 3 + 章节 4
2. 写入 `output/<公司名>_<报告期>_<YYYYMMDD_HHMMSS>.md`
3. 终端打印完成提示和文件路径

---

## 10. 文件结构

```
a-share-analyst/
├── main.py                        # CLI 入口，parse_input + 启动 graph
├── src/
│   ├── agent/
│   │   ├── graph.py               # LangGraph StateGraph 主图定义
│   │   ├── subgraph.py            # data_collection ReAct 子图
│   │   ├── nodes.py               # report_generation / output 节点
│   │   └── state.py               # ReportState / DataCollectionState TypedDict
│   ├── tools/
│   │   ├── structured_data.py     # StructuredDataTool + akshare 接口映射
│   │   ├── search.py              # RealTimeSearchTool (Serper)
│   │   └── calculator.py          # FinancialCalculatorTool
│   └── prompts/
│       ├── data_collection.py     # 数据采购清单 system prompt
│       └── report_sections.py     # 各章节写作 + 验证 prompt 模板
├── data/
│   └── stock_code_map.json        # 公司名 → 股票代码缓存（自动维护）
├── docs/
│   ├── akshare_interfaces/        # 已验证接口 JSON 文件
│   └── superpowers/specs/         # 设计文档
├── output/                        # 生成的研报 .md 文件
└── pyproject.toml
```

---

## 11. 环境变量


| 变量名               | 说明                             |
| ----------------- | ------------------------------ |
| `OPENAI_API_KEY`  | OpenAI Compatible API Key      |
| `OPENAI_BASE_URL` | OpenAI Compatible API Base URL |
| `OPENAI_MODEL`    | 模型名称，默认 `gpt-4o`               |
| `SERPER_API_KEY`  | Serper 搜索 API Key              |


---

## 12. 依赖

在现有 `akshare` 基础上新增：

```
langchain
langchain-openai
langgraph
langchain-community
requests
```

