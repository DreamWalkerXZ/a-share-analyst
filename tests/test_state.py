from src.agent.state import ReportState, DataCollectionState


def test_report_state_keys():
    state: ReportState = {
        "company": "贵州茅台",
        "stock_code": "600519",
        "period": "2025Q4",
        "collected_data": {},
        "sections": {},
        "output_path": "",
    }
    assert state["company"] == "贵州茅台"
    assert state["period"] == "2025Q4"


def test_data_collection_state_keys():
    state: DataCollectionState = {
        "messages": [],
        "collected_data": {},
        "tool_call_count": 0,
    }
    assert state["tool_call_count"] == 0
