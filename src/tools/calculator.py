import math

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

BLOCKED_KEYWORDS = [
    "__import__", "import", "exec", "eval", "open",
    "os", "sys", "subprocess", "builtins",
]


class CalculatorInput(BaseModel):
    expression: str = Field(description="Python 数学表达式，如 '(a - b) / b * 100'")
    variables: dict[str, float] = Field(description="变量名到数值的映射")
    description: str = Field(description="计算目的说明，如 '计算营收同比增速'")


class FinancialCalculatorTool(BaseTool):
    name: str = "financial_calculator"
    description: str = (
        "在受限沙箱中执行财务计算表达式（仅允许 math 模块和四则运算）。"
        "expression 为 Python 数学表达式，variables 为变量值映射。"
    )
    args_schema: type[BaseModel] = CalculatorInput

    def _run(  # type: ignore[override]
        self, expression: str, variables: dict[str, float], description: str
    ) -> dict:
        for keyword in BLOCKED_KEYWORDS:
            if keyword in expression:
                raise ValueError(f"表达式中不允许使用 '{keyword}'")

        steps = f"计算：{description}\n表达式：{expression}\n变量：{variables}"
        allowed_globals = {"__builtins__": {}, "math": math}

        try:
            result = eval(expression, allowed_globals, dict(variables))  # noqa: S307
            steps += f"\n结果：{result}"
            return {"result": float(result), "steps": steps, "description": description}
        except ZeroDivisionError:
            steps += "\n错误：ZeroDivisionError"
            return {"result": None, "steps": steps, "description": description}
        except Exception as exc:
            steps += f"\n错误：{exc}"
            return {"result": None, "steps": steps, "description": description}
