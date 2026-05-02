"""Clean raw research reports by extracting body text with LLM.

Reads markdown files from raw_reports/, uses an LLM to strip noise
(navigation, ads, images, footers, URLs) and output clean markdown
to clean_reports/.

Usage:
    uv run eval/clean_reports.py
    uv run eval/clean_reports.py --dry-run        # preview first report only
    uv run eval/clean_reports.py --limit 5        # process first 5 reports
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

load_dotenv()

RAW_DIR = Path(__file__).parent / "raw_reports"
CLEAN_DIR = Path(__file__).parent / "clean_reports"

EXTRACT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是一位研报清洗助手。你的任务是从财经网站抓取的原始研报中提取正文，并重新组织为整洁的 Markdown 格式。

## 清洗规则
- 去除所有网站导航栏、菜单链接、面包屑、侧边栏、广告和图片。
- 去除所有超链接，但保留链接文字。
- 去除页脚信息："版权所有"、"广告服务"、"联系我们"、"客户服务热线"、"常见问题解答"等。
- 去除元数据行："Title:"、"URL Source:"、"Markdown Content:"、"类别："、"机构："、"研究员："、"日期："等。
- 去除"数据推荐"板块及末尾推荐链接列表。
- 去除原始网页标题后缀（如"__新浪财经_新浪网"）。
- 禁止编造任何元数据，禁止添加机构/研究员/日期等头部信息。

## 格式规则
- 以 `# 报告标题` 开头（使用研报的实际标题作为一级标题）。
- 将正文按以下二级标题（##）分段组织，将原始内容映射到最合适的板块：
  - `## 开篇总览` — 事件概述、核心结论、业绩摘要
  - `## 业绩与经营情况深度拆解` — 营收、利润、各业务线、财务指标分析
  - `## 公司发展展望与核心投资逻辑` — 业务展望、竞争格局、战略方向、增长逻辑
  - `## 盈利预测与估值定价` — 盈利预测、目标价、投资评级
  - `## 风险提示` — 风险因素
- 如果原文中没有对应板块的内容，则省略该标题。
- 每个板块内的正文必须与原文完全一致，禁止改写、概括、转述或修改任何句子。
- 保留原始段落换行和有序/无序列表。"""),
    ("human", "请清洗并重新组织以下原始研报：\n\n{raw_content}"),
])

_model = os.environ.get("OPENAI_MODEL", "gpt-4o")
chain = EXTRACT_PROMPT | ChatOpenAI(model=_model, temperature=0) | StrOutputParser()


def get_report_files(limit: int | None = None) -> list[Path]:
    files = sorted(RAW_DIR.glob("*.md"))
    if limit:
        files = files[:limit]
    return files


def clean_report(raw_path: Path) -> str:
    raw_content = raw_path.read_text(encoding="utf-8")
    return chain.invoke({"raw_content": raw_content})


def main():
    parser = argparse.ArgumentParser(description="Clean raw research reports")
    parser.add_argument("--dry-run", action="store_true", help="Process only the first report and print to stdout")
    parser.add_argument("--limit", type=int, default=None, help="Max number of reports to process")
    args = parser.parse_args()

    files = get_report_files(limit=1 if args.dry_run else args.limit)
    if not files:
        print("No .md files found in raw_reports/")
        sys.exit(1)

    CLEAN_DIR.mkdir(exist_ok=True)

    total = len(files)
    for i, f in enumerate(files, 1):
        print(f"[{i}/{total}] Processing: {f.name}")
        try:
            result = clean_report(f)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        if args.dry_run:
            print("--- Result ---")
            print(result)
            return

        out_path = CLEAN_DIR / f.name
        out_path.write_text(result, encoding="utf-8")
        print(f"  -> Saved to {out_path}")

    print(f"\nDone. {total} reports processed.")


if __name__ == "__main__":
    main()
