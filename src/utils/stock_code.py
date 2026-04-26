import json
from datetime import date, datetime
from pathlib import Path

import akshare as ak

CACHE_PATH = Path("data/stock_code_map.json")
CACHE_TTL_DAYS = 30


def _refresh_cache() -> dict[str, str]:
    df = ak.stock_info_a_code_name()
    mapping: dict[str, str] = dict(zip(df["name"], df["code"]))
    mapping["_updated_at"] = date.today().isoformat()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2))
    return mapping


def _load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        data = json.loads(CACHE_PATH.read_text())
        updated_at = datetime.fromisoformat(data.get("_updated_at", "2000-01-01")).date()
        if (date.today() - updated_at).days <= CACHE_TTL_DAYS:
            return data
    return _refresh_cache()


def lookup_stock_code(company_name: str) -> str:
    mapping = _load_cache()
    code = mapping.get(company_name)
    if not code:
        raise ValueError(
            f"找不到股票代码：{company_name}。"
            "请使用完整公司名，或直接传入股票代码（如 '600519 2025 Q4'）"
        )
    return code
