# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A-share Analyst is an automated equity research report generator for Chinese A-share companies. It collects financial data via AKShare, enriches it with web search (Serper API), then uses an LLM to produce a professional 5-section quarterly earnings review in Markdown.

## Commands

```bash
# Run a report
uv run main.py "贵州茅台 2025 Q4"
uv run main.py "600519 2025 Q4"    # also accepts stock codes directly

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_nodes.py

# Run a single test by name
uv run pytest tests/test_nodes.py::test_generate_section_pass_on_first_attempt -v

# Disable data cache (force fresh API calls)
DISABLE_DATA_CACHE=1 uv run main.py "贵州茅台 2025 Q4"
```

## Environment Variables

Copy `.env.example` to `.env`. Required: `OPENAI_API_KEY`, `SERPER_API_KEY`. The LLM provider is configurable via `OPENAI_BASE_URL` and `OPENAI_MODEL` (defaults to `gpt-4o`). Optional: LangSmith tracing vars.

## Architecture

### Workflow (LangGraph)

Three sequential nodes: `data_collection` → `report_generation` → `output`.

**Data collection** (`src/agent/subgraph.py`) has two phases:
1. **Phase 1 — Pre-fetch**: Calls 6 mandatory AKShare interfaces, filters to target period, parses each via LLM into structured `{key: {label, value, unit, period, source, ...}}` entries, deduplicates by `(label, period)` with source priority.
2. **Phase 2 — ReAct loop**: An inner LangGraph subgraph (`react_reason` ↔ `react_tool`) where the LLM decides which of 3 tools to call next, bounded by `MAX_TOOL_CALLS = 30`. Tool results are parsed inline by LLM on every call and merged into `collected_data`.

**Report generation** (`src/agent/nodes.py`): Generates 5 sections in order `[1,2,3,4,0]` — sections 1–4 first, then section 0 (overview/summary) last so it can reference all others. Each section is validated by a separate LLM call; on failure it retries once, then marks for human review.

**Output**: Assembles sections into Markdown with `DATA_REFS` citation footnotes, saves to `output/`.

### Key Modules

| Module | Role |
|--------|------|
| `src/agent/graph.py` | Top-level 3-node workflow |
| `src/agent/subgraph.py` | Phase 1 pre-fetch + Phase 2 ReAct subgraph |
| `src/agent/nodes.py` | Node implementations, section generation/validation, report assembly |
| `src/agent/state.py` | `ReportState` (top-level) and `DataCollectionState` (subgraph) TypedDicts |
| `src/tools/structured_data.py` | Wraps 40+ AKShare interfaces with file-based cache |
| `src/tools/search.py` | Serper web search for industry/analyst data |
| `src/tools/calculator.py` | Sandboxed financial calculator (simpleeval) |
| `src/utils/llm.py` | `get_llm()` — single LLM factory reading env vars |
| `src/utils/data_cache.py` | SHA1-keyed JSON cache in `data/cache/` with per-action TTL (7d/1d/1h) |
| `src/utils/compact.py` | Formats `collected_data` dict into compact text for LLM prompts |
| `src/utils/prefetch_formatter.py` | Formats raw AKShare JSON for Phase 1 LLM parsing |
| `src/prompts/data_collection.py` | Prompts for Phase 1 and Phase 2 data parsing |
| `src/prompts/report_sections.py` | Section specs (titles, prompts), system prompts, validation prompt |

### Data Flow

```
collected_data: dict[str, dict]
# Key format: "{company}_{period}_{label}" e.g. "贵州茅台_2025Q4_营业收入"
# Value format: {"label": str, "value": float|str, "unit": str, "period": str, "source": str, ...}
```

This dict is the backbone of the system — passed from data collection through all report sections. The `compact_collected()` utility serializes it for LLM context. `DATA_REFS` in generated sections reference these keys for traceability.

### Caching

File-based cache at `data/cache/`. Keyed by `SHA1(action + sorted_params)`. TTL tiers: historical financials (7d), forecasts/research (1d), real-time (1h). Set `DISABLE_DATA_CACHE=1` to bypass.

## Testing

Tests use `pytest` + `pytest-mock`. External dependencies (AKShare, LLM, Serper) are mocked. Test files mirror source structure (`test_nodes.py`, `test_structured_data.py`, `test_subgraph.py`, etc.). Collected data format in tests follows the `{label, value, unit, period, source}` schema.

## Conventions

- Python 3.11+, managed with `uv`
- All LLM interactions go through `src/utils/llm.py::get_llm()`
- AKShare interfaces are added via `INTERFACE_MAP` in `structured_data.py`; each entry is a `lambda p: ak.xxx(**p)` taking a params dict
- Report section order in generation is `[section_1..4, section_0]`; in output assembly it's `[section_0..4]`
- Symbol formats vary by API: `SH600519` (East Money), `600519.SH` (some EM endpoints), `600519` (plain)
