# AGENTS.md

This file gives Codex agents repository-specific guidance for working on
`a-share-analyst`.

## Project Summary

`a-share-analyst` is an automated Chinese A-share equity research report
generator. The CLI accepts a company name or 6-digit stock code plus a year and
quarter, collects financial and market data through AKShare and Serper, then
uses an LLM workflow to produce a professional quarterly earnings review in
Markdown.

The main product output is a 5-section report saved under `output/`.

## Common Commands

```bash
# Generate a report
uv run main.py "贵州茅台 2025 Q4"
uv run main.py "600519 2025 Q4"

# Run tests
uv run pytest
uv run pytest tests/test_nodes.py
uv run pytest tests/test_nodes.py::test_generate_section_pass_on_first_attempt -v

# Bypass AKShare cache for fresh data
DISABLE_DATA_CACHE=1 uv run main.py "贵州茅台 2025 Q4"
```

Environment variables are loaded from `.env` via `python-dotenv`. Required keys
are `OPENAI_API_KEY` and `SERPER_API_KEY`. The LLM endpoint/model can be changed
with `OPENAI_BASE_URL` and `OPENAI_MODEL`; the default model is `gpt-4o`.

## High-Level Architecture

The top-level workflow is a LangGraph graph in `src/agent/graph.py`:

```text
data_collection -> report_generation -> output
```

Key files:

| Path | Responsibility |
| --- | --- |
| `main.py` | CLI parsing, company/code normalization, graph invocation |
| `src/agent/graph.py` | Top-level LangGraph wiring |
| `src/agent/state.py` | `ReportState` and `DataCollectionState` TypedDicts |
| `src/agent/subgraph.py` | Two-phase data collection workflow |
| `src/agent/nodes.py` | Graph node implementations, section generation/validation, report assembly |
| `src/tools/structured_data.py` | AKShare interface wrapper and web/PDF fetch sentinel |
| `src/tools/search.py` | Serper real-time web search tool |
| `src/tools/calculator.py` | Sandboxed financial calculator |
| `src/utils/llm.py` | Single LLM factory; all LLM calls should go through `get_llm()` |
| `src/utils/data_cache.py` | File-based AKShare cache under `data/cache/` |
| `src/utils/compact.py` | Compact serialization of `collected_data` for prompts |
| `src/utils/prefetch_formatter.py` | Pre-fetch JSON formatting for LLM parsing |
| `src/prompts/data_collection.py` | Data collection prompts |
| `src/prompts/report_sections.py` | Section prompts and validation prompt |

## Data Collection

Data collection lives in `src/agent/subgraph.py` and has two phases.

Phase 1 pre-fetches mandatory AKShare interfaces listed in `PREFETCH_ACTIONS`.
It filters raw records to the target period cutoff, caps records/chars to
protect context length, then asks the LLM to parse each source into
`collected_data` entries. Duplicate `(label, period)` entries are resolved by
`_SOURCE_PRIORITY`.

Phase 2 is a ReAct subgraph:

```text
react_reason <-> react_tool
```

The LLM chooses among:

- `structured_data`
- `realtime_search`
- `financial_calculator`

Each tool result is parsed immediately by an LLM call into structured entries
and merged into `collected_data`. The loop is bounded by `MAX_TOOL_CALLS = 30`.
If the model/provider cannot tool-call or the request fails due to model/context
issues, the ReAct phase exits early with the data already collected.

## Core Data Contract

`collected_data` is the backbone of the system. Preserve this shape:

```python
{
    "{company}_{period}_{label}": {
        "label": str,
        "value": float | int | str,
        "unit": str,
        "period": str,
        "source": str,
        # optional metadata fields
    }
}
```

Report prompts and validators depend on exact keys. When changing parsing,
formatting, or prompts, keep keys stable and traceable. Section output must end
with a `DATA_REFS:` line containing exact `collected_data` keys; `nodes.py`
strips that line and appends a human-readable data footnote.

## Report Generation

Report generation is in `src/agent/nodes.py`.

Sections are generated in this order:

```python
["section_1", "section_2", "section_3", "section_4", "section_0"]
```

`section_0` is the title/opening summary and is generated last so it can refer
to the already generated analytical sections. Final assembly order is:

```python
["section_0", "section_1", "section_2", "section_3", "section_4"]
```

Each section is validated with a separate LLM call. If validation fails, the
section is regenerated once with correction requirements. If retry validation
still fails, a manual-review warning is appended.

Do not reorder chapters unless the user explicitly asks; the current order is
part of the report quality strategy.

## AKShare Interfaces

AKShare interfaces are registered in `INTERFACE_MAP` in
`src/tools/structured_data.py`. Add new interfaces there as:

```python
"get_some_action": lambda p: ak.some_api(**p)
```

Keep the tool contract as `action` plus `params`. Most actions require a
non-empty `params` dict. Symbol formats differ by endpoint:

- `SH600519` / `SZ000001` for many East Money interfaces
- `600519.SH` / `000001.SZ` for some report-period endpoints
- `600519` for some 同花顺/CNInfo-style endpoints

When adding an interface, also consider:

- whether it should be pre-fetched in `PREFETCH_ACTIONS`
- whether it needs special symbol handling in `subgraph.py`
- whether it should be cached and with which TTL in `data_cache.py`
- whether docs or JSON interface references under `docs/akshare_interfaces/`
  should be updated

## Cache Behavior

AKShare responses are cached as JSON envelopes in `data/cache/`, keyed by
`SHA1(action + sorted params)`.

TTL tiers:

- stable historical financials: 7 days
- forecasts, research, peer/industry data: 1 day
- real-time/sentiment data: 1 hour

Set `DISABLE_DATA_CACHE=1` to force fresh calls. Do not commit generated cache
or report output unless the user explicitly wants artifacts committed.

## Testing Notes

Tests use `pytest` and `pytest-mock`. External services such as AKShare, Serper,
and LLM providers should be mocked in tests. Test files mirror source modules:

- `tests/test_main.py`
- `tests/test_graph.py`
- `tests/test_subgraph.py`
- `tests/test_nodes.py`
- `tests/test_structured_data.py`
- `tests/test_search.py`
- `tests/test_calculator.py`
- `tests/test_data_cache.py`
- `tests/test_stock_code.py`

For narrow changes, run the matching test file first. For workflow or data
contract changes, run the full suite with `uv run pytest`.

## Coding Conventions

- Use Python 3.11+ and `uv`.
- Keep all LLM construction centralized through `src/utils/llm.py::get_llm()`.
- Keep LangGraph state compatible with `ReportState` and
  `DataCollectionState`.
- Prefer structured JSON parsing over ad hoc string parsing when handling model
  or API outputs.
- Preserve exact `DATA_REFS` behavior when editing report generation.
- Keep output Markdown generation deterministic outside of LLM content.
- Avoid broad refactors when fixing a local bug; this repo has many prompt and
  data-contract couplings.
- Generated files under `output/` and cache files under `data/cache/` are
  runtime artifacts.

## Typical Change Workflow For Codex

1. Inspect the relevant module and its matching tests.
2. Identify whether the change affects the data contract, prompt contract, graph
   ordering, cache behavior, or external tool schema.
3. Make the smallest code change that fits existing patterns.
4. Add or update focused tests for parsing, state transitions, or tool behavior.
5. Run targeted tests, then `uv run pytest` if the change touches shared
   workflow code.
6. In the final response, mention changed files and which tests were run.

## Main Footguns

- Do not invent `collected_data` keys in report content; keys must match exactly.
- Do not bypass `get_llm()` with a separate LLM client.
- Do not remove the Phase 1 pre-fetch without replacing its coverage; later
  prompts assume core financial data is already present.
- Do not let raw AKShare payloads grow without caps; context length is a real
  failure mode.
- Do not silently change stock symbol formats across endpoints.
- Do not treat generated reports as tests; unit tests should mock external
  services and assert state/data behavior.
