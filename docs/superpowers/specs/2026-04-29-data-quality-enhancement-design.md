# Data Quality Enhancement Design

## Problem

The collected_data JSON covers ~30 absolute financial values (revenue, profit, margins) but misses entire categories of data that professional equity research reports require:

1. **Full-year cash flow** (only Q4 single-quarter, missing annual cumulative)
2. **Derived metrics** (expense ratios, dividend payout ratio, margin pct-point changes)
3. **Channel/product granularity** (wholesale vs direct vs online channel revenue, sales volume, ton-price, distributor count) — AKShare has no structured APIs for these
4. **Qualitative/forward-looking data** (price changes, wholesale price trends, next-quarter outlook)

Note: YoY growth rates for core metrics (revenue, profit, margins) are **already covered** by the `financial_indicators_em` quarterly interface which returns `*_YOY` fields. This was confirmed by running a live prefetch — no change needed for YoY rates.

## Solution Overview

Three targeted changes across prefetch, computation, and prompt layers.

---

## Change 1: Add `get_cashflow_report` to Prefetch

**File**: `src/agent/subgraph.py`

**Current state**: `PREFETCH_ACTIONS` includes `get_cashflow_quarterly` (single-quarter) but not `get_cashflow_report` (cumulative). Verified by live run:

| Data | Report needs | Current (`cashflow_quarterly`) | With `cashflow_report` |
|---|---|---|---|
| 2025 sales cash flow | 1839.9B | 451.99B (Q4 only) | **1839.9B** (full-year) |
| Sales cash flow YoY | +0.7% | missing | **+0.74%** (SALES_SERVICES_YOY) |
| 2025 operating cash flow | 615.2B | 233.25B (Q4 only) | **615.2B** (full-year) |
| Operating cash flow YoY | -33.5% | missing | **-33.46%** (NETCASH_OPERATE_YOY) |

The `get_cashflow_report` API (`stock_cash_flow_sheet_by_report_em`) returns 254 columns including `*_YOY` fields.

**Change**:
- Add `"get_cashflow_report"` to `PREFETCH_ACTIONS` list
- Add it to `_ANNUAL_ACTIONS` set so it uses `PREFETCH_MAX_RECORDS_ANNUAL = 1`
- Add a formatter case in `prefetch_formatter.py` for `cashflow_report` (reuse `_CASHFLOW_FIELDS` map, note it's cumulative annual data)

---

## Change 2: Auto-Derive Metrics After Phase 1

**File**: New function `auto_derive_metrics()` in `src/agent/subgraph.py`

**When**: Called after `_parse_prefetched()` and before building Phase 2 initial state.

**What it does**: Scans `collected_data` and computes derived metrics using pure Python arithmetic (no LLM). Results are merged into `collected_data` with source `"auto_derived"`.

### 2a. Expense Ratios

For known expense items, compute ratio against operating revenue. Only for periods where both numerator and denominator exist.

| Expense Item | Formula | New Label |
|---|---|---|
| 营业税金及附加 | value / 营业总收入 * 100 | 税金及附加率 |
| 销售费用 | value / 营业总收入 * 100 | 销售费用率 |
| 管理费用 | value / 营业总收入 * 100 | 管理费用率 |

Key format: `{company}_{period}_{label}率`, e.g. `贵州茅台_2025年_销售费用率`

### 2b. Dividend Payout Ratio

- Payout ratio = total dividend per share (年度 + 季度) / annual EPS * 100
- Requires: entries with label `年度分红_派息比例`, `季度分红_派息比例`, `基本每股收益` (annual)
- Only compute if all inputs exist

Key format: `{company}_{period}_分红率`

### 2c. Margin Pct-Point Changes

For margin-type metrics already in `collected_data` (毛利率, 净利率, 费用率), compute period-over-period change in percentage points by matching against the prior-year period's value.

Period matching: strip year from period, subtract 1.
- `2025年` → prior `2024年`
- `2025Q4` → prior `2024Q4`

```
margin_change_pp = current_margin_pct - prior_margin_pct
```

Key format: `{company}_{period}_{label}_变动`, unit="pct"

Skip if prior value missing, or if both values are from the same period.

### What is NOT auto-derived (and why)

| Metric | Why not |
|---|---|
| Revenue/profit YoY growth | Already provided by `financial_indicators_em` quarterly `*_YOY` fields |
| Gross margin YoY | Already in `GROSS_PROFIT_RATIO` field (quarterly indicators include YoY for margins too — but actually this is a level, not a YoY change; however the `TOTALOPERATEREVETZ`/`PARENTNETPROFITTZ` fields give revenue/profit YoY, and margin pct-point change is handled by 2c above) |
| Channel/volume metrics | Not available from structured data; handled by Change 3 (web search) |

---

## Change 3: Enhance Phase 2 System Prompt

**File**: `src/prompts/data_collection.py`

### 3a. Add Industry-Agnostic Data Gap Guidance

Add a section to `PHASE2_SYSTEM_PROMPT` that informs the LLM about known data gaps organized by industry sector. The LLM should pick the relevant section based on the target company's industry:

```
■ 各行业通用
- 费用明细、产能与资本开支、管理层经营指引

■ 消费/零售/食品饮料
- 渠道拆分（批发/直销/线上）、产销量/吨价、经销商/门店、批价/提价

■ 金融（银行/保险/券商）
- 净息差、不良率、拨备覆盖率、资本充足率、保费收入、AUM

■ 医药生物
- 研发管线、临床试验进展、BD/授权合作、医保谈判/集采

■ 信息技术/互联网/SaaS
- 订阅续费率（NDR）、ARPU、用户/客户数、云收入占比、研发投入

■ 能源/原材料/工业制造
- 量价数据（产量、售价）、订单/库存、产能扩张/资本开支
```

Generic search templates are provided (not company-specific).

### 3b. Add Auto-Derive Notice

Add a note so the LLM knows what's already been computed:

```
【自动计算已完成】费用率、分红率、毛利率/净利率变动等衍生指标已在预取阶段自动计算完成，
已纳入下方已收集数据。如需额外计算（如吨价=收入/销量），使用 financial_calculator。
```

### 3c. Prioritize Web Search for Non-Standard Data

Update the priority ordering in the prompt to reflect that non-standard data (channel, volume, distributor) should be sought via `realtime_search` early in Phase 2, not as a last resort.

---

## Changes NOT Made (and why)

| Idea | Rejected Because |
|---|---|
| `PREFETCH_MAX_RECORDS` 2→3 | Tested: max_records=3 yields 2025Q4+Q3+Q2, not 2024Q4. Filter sorts by date descending so extra records are same-year earlier quarters, not prior year |
| `stock_zygc_ym` (益盟主营同比) | API does not exist in current AKShare version |
| `stock_cash_flow_sheet_by_yearly_em` | Redundant with `get_cashflow_report` (same 254 columns, YOY fields) |
| Annual report web scraping (F10/PDF) | Too fragile, high maintenance burden, not reliably parseable across companies |
| Auto-derive YoY for income statement items | `financial_indicators_em` quarterly already provides `TOTALOPERATEREVETZ`, `PARENTNETPROFITTZ` etc.; duplicating adds no value |

---

## Files Modified

| File | Change |
|---|---|
| `src/agent/subgraph.py` | Add `get_cashflow_report` to `PREFETCH_ACTIONS` and `_ANNUAL_ACTIONS`; add `auto_derive_metrics()` function; call it in `run_data_collection()` |
| `src/utils/prefetch_formatter.py` | Add formatter case for `cashflow_report` (cumulative annual, reuse `_CASHFLOW_FIELDS`) |
| `src/prompts/data_collection.py` | Add data gap guidance and auto-derive notice to `PHASE2_SYSTEM_PROMPT` |

## Testing Strategy

- Unit test `auto_derive_metrics()` with fixture data covering: expense ratio computation, dividend payout, margin pct-point change, and edge cases (zero prior, missing data, no matching prior period)
- Integration test: run Phase 1 prefetch on 贵州茅台 2025Q4 and verify collected_data contains: full-year cash flow with YoY, expense ratios, dividend payout ratio
- Verify no regressions: existing test suite still passes
