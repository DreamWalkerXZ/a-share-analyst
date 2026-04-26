import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.utils.stock_code import lookup_stock_code


def test_lookup_existing_code(tmp_path, monkeypatch):
    cache = {"贵州茅台": "600519", "_updated_at": "2026-04-26"}
    cache_file = tmp_path / "stock_code_map.json"
    cache_file.write_text(json.dumps(cache, ensure_ascii=False))
    monkeypatch.setattr("src.utils.stock_code.CACHE_PATH", cache_file)
    assert lookup_stock_code("贵州茅台") == "600519"


def test_lookup_missing_raises(tmp_path, monkeypatch):
    cache = {"招商银行": "600036", "_updated_at": "2026-04-26"}
    cache_file = tmp_path / "stock_code_map.json"
    cache_file.write_text(json.dumps(cache, ensure_ascii=False))
    monkeypatch.setattr("src.utils.stock_code.CACHE_PATH", cache_file)
    with pytest.raises(ValueError, match="找不到股票代码"):
        lookup_stock_code("不存在公司")


def test_cache_auto_created_when_missing(tmp_path, monkeypatch):
    cache_file = tmp_path / "stock_code_map.json"
    monkeypatch.setattr("src.utils.stock_code.CACHE_PATH", cache_file)
    mock_df = pd.DataFrame({"name": ["贵州茅台", "招商银行"], "code": ["600519", "600036"]})
    with patch("akshare.stock_info_a_code_name", return_value=mock_df):
        result = lookup_stock_code("贵州茅台")
    assert result == "600519"
    assert cache_file.exists()
