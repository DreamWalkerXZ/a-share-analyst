import os
import pytest
from unittest.mock import MagicMock, patch

from src.tools.search import RealTimeSearchTool


def test_search_returns_formatted_results(mocker):
    tool = RealTimeSearchTool()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "organic": [
            {"title": "茅台年报点评", "snippet": "净利润同比增长12%", "link": "https://a.com"},
            {"title": "茅台估值分析", "snippet": "PE约28倍低于历史均值", "link": "https://b.com"},
        ]
    }
    mock_resp.raise_for_status = MagicMock()
    mocker.patch("requests.post", return_value=mock_resp)
    with patch.dict(os.environ, {"SERPER_API_KEY": "test-key"}):
        result = tool._run(query="贵州茅台2025年报分析")
    assert "茅台年报点评" in result
    assert "净利润同比增长12%" in result


def test_search_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    tool = RealTimeSearchTool()
    with pytest.raises(ValueError, match="SERPER_API_KEY"):
        tool._run(query="test")
