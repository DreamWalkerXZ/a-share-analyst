import pytest
from unittest.mock import patch

from main import parse_input


def test_parse_company_name_and_period():
    with patch("main.lookup_stock_code", return_value="600519"):
        result = parse_input("贵州茅台 2025 Q4")
    assert result == {"company": "贵州茅台", "stock_code": "600519", "period": "2025Q4"}


def test_parse_numeric_stock_code_directly():
    result = parse_input("600519 2025 Q4")
    assert result == {"company": "600519", "stock_code": "600519", "period": "2025Q4"}


def test_parse_q1_period():
    with patch("main.lookup_stock_code", return_value="600036"):
        result = parse_input("招商银行 2025 Q1")
    assert result["period"] == "2025Q1"


def test_invalid_format_raises():
    with pytest.raises(ValueError, match="输入格式错误"):
        parse_input("贵州茅台")


def test_invalid_quarter_raises():
    with patch("main.lookup_stock_code", return_value="600519"):
        with pytest.raises(ValueError, match="无效季度"):
            parse_input("贵州茅台 2025 Q5")
