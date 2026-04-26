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
    mocker.patch("src.agent.nodes.get_llm", return_value=_make_llm([gen_json, val_json]))

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
        "src.agent.nodes.get_llm",
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
