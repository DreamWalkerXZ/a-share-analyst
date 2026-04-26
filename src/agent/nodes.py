import json
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.state import ReportState
from src.agent.subgraph import run_data_collection
from src.prompts.report_sections import SECTION_PROMPTS, SECTION_SYSTEM_PROMPT, VALIDATION_PROMPT
from src.utils.llm import get_llm


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
    llm = get_llm()
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
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
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
