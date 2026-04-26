import math

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from simpleeval import EvalWithCompoundTypes, FunctionNotDefined, NameNotDefined, NumberTooHigh


class _MathNS:
    """Non-module namespace that exposes math functions as attributes."""


for _name in dir(math):
    if not _name.startswith("_"):
        setattr(_MathNS, _name, staticmethod(getattr(math, _name)))

_MATH_NS = _MathNS()


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
        steps = f"计算：{description}\n表达式：{expression}\n变量：{variables}"
        evaluator = EvalWithCompoundTypes(names={**variables, "math": _MATH_NS})

        try:
            result = evaluator.eval(expression)
            steps += f"\n结果：{result}"
            return {"result": float(result), "steps": steps, "description": description}
        except ZeroDivisionError:
            steps += "\n错误：ZeroDivisionError"
            return {"result": None, "steps": steps, "description": description}
        except (NameNotDefined, FunctionNotDefined, NumberTooHigh, ValueError) as exc:
            steps += f"\n错误：{exc}"
            raise ValueError(f"表达式中不允许使用 '{exc}'") from exc
        except Exception as exc:
            steps += f"\n错误：{exc}"
            return {"result": None, "steps": steps, "description": description}
