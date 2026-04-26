import os

import requests
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

SERPER_URL = "https://google.serper.dev/search"


class SearchInput(BaseModel):
    query: str = Field(description="搜索查询词")


class RealTimeSearchTool(BaseTool):
    name: str = "realtime_search"
    description: str = (
        "使用 Serper 搜索引擎获取实时信息。"
        "用于 akshare 无法覆盖的行业数据、分析师预期、可比公司估值等。"
    )
    args_schema: type[BaseModel] = SearchInput

    def _run(self, query: str) -> str:  # type: ignore[override]
        api_key = os.environ.get("SERPER_API_KEY")
        if not api_key:
            raise ValueError("SERPER_API_KEY 环境变量未设置")

        resp = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": 5},
            timeout=15,
        )
        resp.raise_for_status()

        results = []
        for item in resp.json().get("organic", []):
            results.append(
                f"**{item.get('title', '')}**\n"
                f"{item.get('snippet', '')}\n"
                f"URL: {item.get('link', '')}"
            )
        return "\n\n".join(results) if results else "无搜索结果"
