from typing import TypedDict

from langchain_core.messages import BaseMessage


class ReportState(TypedDict):
    company: str
    stock_code: str
    period: str
    collected_data: dict
    sections: dict
    output_path: str


class DataCollectionState(TypedDict):
    messages: list[BaseMessage]
    collected_data: dict
    tool_call_count: int
