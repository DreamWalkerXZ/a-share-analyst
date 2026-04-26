import math
import pytest

from src.tools.calculator import FinancialCalculatorTool


def test_yoy_growth_calculation():
    tool = FinancialCalculatorTool()
    result = tool._run(
        expression="(current - prev) / prev * 100",
        variables={"current": 423.58, "prev": 377.05},
        description="营收同比增速",
    )
    assert abs(result["result"] - 12.34) < 0.01
    assert "current" in result["steps"]
    assert result["description"] == "营收同比增速"


def test_math_functions_allowed():
    tool = FinancialCalculatorTool()
    result = tool._run(
        expression="math.sqrt(value)",
        variables={"value": 9.0},
        description="开方测试",
    )
    assert result["result"] == 3.0


def test_blocked_import_raises():
    tool = FinancialCalculatorTool()
    with pytest.raises(ValueError, match="不允许"):
        tool._run(
            expression="__import__('os').system('ls')",
            variables={},
            description="恶意代码",
        )


def test_division_by_zero_returns_none():
    tool = FinancialCalculatorTool()
    result = tool._run(
        expression="a / b",
        variables={"a": 100.0, "b": 0.0},
        description="除以零",
    )
    assert result["result"] is None
    assert "ZeroDivisionError" in result["steps"]
