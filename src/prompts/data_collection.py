PHASE2_PARSE_PROMPT = """\
你是一位专业的 A 股研究员，正在为 {company}（{stock_code}）{period} 补充研报数据。

本次工具调用信息：
- 工具名称：{tool_name}
- 调用参数：{tool_args}

工具返回的原始结果：
{raw_data}

当前已收集的数据（共 {existing_count} 条，请勿重复收录；格式：key | 字段名: 值 单位 (报告期)）：
{existing_summary}

请将原始结果中的有价值数据提炼为 collected_data 条目。

筛选规则：
1. 跳过与已收集数据重复或高度相似的指标（同报告期同指标，即使数值略有差异）
2. 仅采纳可信度高的数据：
   - structured_data / financial_calculator 结果：直接采纳
   - realtime_search 结果：只采纳有明确来源（机构名、报告日期）的数据；\
纯预测或无来源的数据加注 notes 说明，不确定则跳过
3. 优先收录补充性数据：同行对比、盈利预测、分红历史、行业 PE/景气度、估值快照

每条条目格式：
{{"KEY": {{"label": "中文语义名称", "value": 数值（数字类型，非字符串）, "unit": "单位",
  "period": "数据所属报告期", "source": "{tool_name}-{tool_args_brief}",
  "raw_field": "原始字段名（无则留空）", "notes": "可选说明"}}}}

KEY 和单位规则：
- KEY 格式："{{公司名}}_{{数据实际报告期}}_{{指标名}}"（用数据本身的报告期，非目标期 {period}）
  - 季度财务数据 → period 用 2025Q4，如 "{company}_2025Q4_归母净利润"
  - 年度历史数据 → period 用 2025年，如 "{company}_2025年_EPS"
  - 分析师预测   → period 用预测年份，如 "{company}_2026_EPS预测均值"
  - 估值快照     → period 用日期，如 "{company}_20260424_PE_TTM"
  - 行业/对比数据 → 含公司/行业名称，如 "白酒行业_2025Q4_营收增速"
- 货币金额单位：亿元（若原始为元，须÷1e8；若已是亿元，直接填写）
- 每股指标单位：元/股（禁止÷1e8，直接抄写）
- 增速/比率单位：%（已是百分比的直接填写，小数形式需×100）
- 市盈率/市净率：倍

如无可提炼的新数据，返回空 JSON 对象 {{}}。
仅输出 JSON，不加任何解释。

输出：
```json
{{...}}
```
"""

PHASE1_PARSE_PROMPT = """\
你是一位专业的 A 股研究员，负责将以下已整理好的财务数据提炼为 collected_data 条目。

公司：{company}（股票代码：{stock_code}）
目标报告期：{period}

重要说明：以下数据已经过预处理。
- 所有货币金额已换算为亿元（不是元，不是万元，是亿元）。
- 每股指标标注了"[每股值，元/股]"，单位为元/股。
- 百分比指标单位为 %。
- 你必须直接使用数据中显示的数值，禁止自行换算或乘除。

整理好的数据：
{raw_data}

请仅提取与【目标报告期 {period}】匹配的报告期的字段，生成如下格式的 collected_data：
{{
  "KEY": {{
    "label": "中文语义名称",
    "value": 数值（直接复制数据中的数字，禁止换算）,
    "unit": "单位（亿元 / % / 元/股，严格按字段标注）",
    "period": "报告期字符串（季度如 2025Q4，年度如 2025年）",
    "source": "来源接口名（数据开头的'来源：...'字段）",
    "raw_field": "",
    "notes": "可选说明"
  }}
}}

KEY 格式："{company}_{{period}}_{{指标名}}"，如 "贵州茅台_2025Q4_归母净利润"

规则：
1. 字段标注"[每股值，元/股]"→ unit="元/股"，value 直接抄写，禁止÷1e8
2. 字段标注"[总量]"→ unit="亿元"，value 直接抄写（已是亿元）
3. 字段含"[注意]"→ 在 notes 中记录
4. 累计年度数据（来源含"累计年度"）的 period 填"2025年"，不填"2025Q4"
5. 主营业务数据的 period 填"2025年"
6. 仅输出 JSON，不加任何解释

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
