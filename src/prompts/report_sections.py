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
