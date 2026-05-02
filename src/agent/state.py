from typing import TypedDict

from langchain_core.messages import BaseMessage


class ReportState(TypedDict):
    company: str
    stock_code: str
    period: str
    collected_data: dict
    sections: dict
    output_path: str
    eval_output_dir: str  # optional: override output directory for batch eval
    eval_prefix: str  # optional: override filename prefix for batch eval


class DataCollectionState(TypedDict):
    messages: list[BaseMessage]
    collected_data: dict
    tool_call_count: int
    company: str
    stock_code: str
    period: str
