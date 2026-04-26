import json
import re
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.state import ReportState
from src.agent.subgraph import run_data_collection
from src.prompts.report_sections import SECTION_PROMPTS, SECTION_SYSTEM_PROMPT, VALIDATION_PROMPT
from src.utils.llm import get_llm



_DATA_REFS_RE = re.compile(r"<!--\s*DATA_REFS:\s*(.*?)\s*-->", re.IGNORECASE | re.DOTALL)


def _parse_section_response(content: str) -> tuple[str, list[str]]:
    """Extract Markdown text and data refs from LLM response.

    Prompts ask for plain Markdown with a trailing <!-- DATA_REFS: ... --> comment.
    Also handles legacy JSON-wrapped responses for backward compatibility.
    Returns (markdown_content_with_refs_footnote, data_refs_list).
    """
    data_refs: list[str] = []

    # --- Legacy JSON fallback (some models still wrap output in JSON) ---
    if "```json" in content:
        json_str = content.split("```json")[1].split("```")[0].strip()
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, dict) and "content" in parsed:
                content = parsed["content"]
                data_refs = parsed.get("data_refs", [])
        except json.JSONDecodeError:
            # Malformed JSON (likely unescaped newlines in content value).
            start = json_str.find('"content"')
            if start >= 0:
                quote = json_str.find('"', start + len('"content"') + 1)
                end = json_str.rfind('", "data_refs"')
                if 0 < quote < end:
                    raw = json_str[quote + 1:end]
                    content = raw.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\").strip()

    elif content.strip().startswith("{"):
        try:
            parsed = json.loads(content.strip())
            if isinstance(parsed, dict) and "content" in parsed:
                content = parsed["content"]
                data_refs = parsed.get("data_refs", [])
        except json.JSONDecodeError:
            pass

    # --- Extract <!-- DATA_REFS: ... --> comment from plain-Markdown responses ---
    if not data_refs:
        m = _DATA_REFS_RE.search(content)
        if m:
            refs_str = m.group(1).strip()
            data_refs = [r.strip() for r in refs_str.split(",") if r.strip()]
            content = _DATA_REFS_RE.sub("", content).rstrip()

    # Append a visible footnote so reviewers can trace every data point.
    if data_refs:
        content += "\n\n> *数据引用：* " + " · ".join(data_refs)

    return content, data_refs


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
    data_json = json.dumps(collected_data, ensure_ascii=False, indent=2)
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
        # Strip the data-refs footnote line before validation to avoid noise.
        content_for_validation = re.sub(
            r"\n\n> \*数据引用：\*.*$", "", content, flags=re.DOTALL
        ).rstrip()
        prompt = VALIDATION_PROMPT.format(content=content_for_validation, data_subset=data_json)
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
    top_issues = "; ".join(i[:120] for i in retry_issues[:3])
    suffix = f"（共 {len(retry_issues)} 项）" if len(retry_issues) > 3 else ""
    return retry_content + f"\n\n> ⚠️ 需要人工验证{suffix}：{top_issues}"


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
