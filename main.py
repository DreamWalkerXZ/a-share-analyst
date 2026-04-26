import re
import sys

from dotenv import load_dotenv

from src.utils.stock_code import lookup_stock_code

load_dotenv()

VALID_QUARTERS = {"Q1", "Q2", "Q3", "Q4"}


def parse_input(raw: str) -> dict:
    """Parse CLI input like "贵州茅台 2025 Q4" into {company, stock_code, period}.

    Also accepts "600519 2025 Q4" when the first token is a 6-digit stock code.
    """
    parts = raw.strip().split()
    if len(parts) < 3:
        raise ValueError(f"输入格式错误，期望：'公司名 年份 季度'，实际收到：{raw!r}")

    quarter = parts[-1].upper()
    year = parts[-2]
    company = " ".join(parts[:-2])

    if quarter not in VALID_QUARTERS:
        raise ValueError(f"无效季度 {quarter!r}，应为 Q1/Q2/Q3/Q4")

    period = f"{year}{quarter}"

    if re.fullmatch(r"\d{6}", company):
        stock_code = company
    else:
        stock_code = lookup_stock_code(company)

    return {"company": company, "stock_code": stock_code, "period": period}


def main():
    if len(sys.argv) < 2:
        print('用法：uv run main.py "公司名 年份 季度"')
        print('示例：uv run main.py "贵州茅台 2025 Q4"')
        sys.exit(1)

    params = parse_input(sys.argv[1])
    print(f"[main] 解析完成：{params}")

    from src.agent.graph import build_graph  # noqa: PLC0415 — lazy import avoids slow startup

    graph = build_graph()
    initial_state = {
        "company": params["company"],
        "stock_code": params["stock_code"],
        "period": params["period"],
        "collected_data": {},
        "sections": {},
        "output_path": "",
    }
    final_state = graph.invoke(initial_state)
    print(f"\n研报生成完成：{final_state['output_path']}")


if __name__ == "__main__":
    main()
