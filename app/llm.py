from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from .data import EMPLOYEES
from .models import DAYS, HardConstraint, Plan, ScheduleState, SoftConstraint


def _load_local_env() -> None:
    path = os.getenv("SCHEDULE_ENV_FILE", ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_local_env()


@dataclass(frozen=True)
class LLMSettings:
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    timeout_seconds: float = 20.0
    required: bool = False

    @classmethod
    def from_env(cls) -> "LLMSettings":
        if os.getenv("SCHEDULE_LLM_API_KEY"):
            return cls(
                os.getenv("SCHEDULE_LLM_API_KEY", ""),
                os.getenv("SCHEDULE_LLM_BASE_URL", "").rstrip("/"),
                os.getenv("SCHEDULE_LLM_MODEL", ""),
                float(os.getenv("SCHEDULE_LLM_TIMEOUT", "20")),
                os.getenv("SCHEDULE_LLM_REQUIRED", "false").lower() == "true",
            )
        if os.getenv("ARK_API_KEY"):
            return cls(os.getenv("ARK_API_KEY", ""), os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/"), os.getenv("ARK_MODEL", ""))
        if os.getenv("DEEPSEEK_API_KEY"):
            return cls(os.getenv("DEEPSEEK_API_KEY", ""), "https://api.deepseek.com/v1", os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))
        if os.getenv("OPENAI_API_KEY"):
            return cls(os.getenv("OPENAI_API_KEY", ""), os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"), os.getenv("OPENAI_MODEL", ""))
        return cls()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


class PlannerModelError(RuntimeError):
    pass


class PlannerNeedsClarification(RuntimeError):
    pass


def _employee_context() -> list[dict[str, Any]]:
    return [{
        "id": e.employee_id,
        "position": e.position,
        "skills": sorted(e.skills),
        "available_days": [d for d in DAYS if d in e.available_days],
        "leave_days": [d for d in DAYS if d in e.leave_days],
        "preference": e.preference,
    } for e in EMPLOYEES]


SYSTEM_PROMPT = """你是智能排班系统的 Planner Agent。你不直接编造排班，只把用户需求转换为求解器可执行的结构化计划。

硬约束只能是：
- unavailable：员工某日或某班绝对不可排
- must_assign：员工某日某班必须安排

软约束只能是：
- prefer_assignment：用户表达“尽量、优先、希望”时使用

决策规则：
1. “不能、不可、请假”是硬约束；“必须、务必、一定”是硬约束；“尽量、优先、希望”是软约束。
2. 不得给员工补充员工数据中没有的技能，不得修改 R-01 至 R-09。
3. 用户说“取消/删除之前的偏好或限制”时，operation=remove；否则 operation=add。
4. 如果员工、日期、班次或意图无法确定，action=clarify，并给出 clarification_question；不要猜。
5. “重新生成/强制重排”令 force=true，普通“生成”令 force=false。
6. 一般生成请求没有额外约束时，hard_constraints 和 soft_constraints 都为空。

只输出一个 JSON 对象，不要 Markdown：
{"action":"generate","force":false,"hard_constraints":[{"operation":"add","kind":"unavailable","employee_id":"E01","day":"周一","shift":null}],"soft_constraints":[{"operation":"add","kind":"prefer_assignment","employee_id":"E01","day":"周一","shift":"早班"}],"clarification_question":null,"summary":"一句话说明计划"}
"""


FEW_SHOTS = [
    ("E07 周五晚班不能排，请重新排班", {"action": "modify", "force": False, "hard_constraints": [{"operation": "add", "kind": "unavailable", "employee_id": "E07", "day": "周五", "shift": "晚班"}], "soft_constraints": [], "clarification_question": None, "summary": "新增 E07 周五晚班禁排约束后求解"}),
    ("尽量让 E09 周六上早班", {"action": "modify", "force": False, "hard_constraints": [], "soft_constraints": [{"operation": "add", "kind": "prefer_assignment", "employee_id": "E09", "day": "周六", "shift": "早班"}], "clarification_question": None, "summary": "新增 E09 周六早班偏好"}),
    ("取消 E09 周六早班优先", {"action": "modify", "force": False, "hard_constraints": [], "soft_constraints": [{"operation": "remove", "kind": "prefer_assignment", "employee_id": "E09", "day": "周六", "shift": "早班"}], "clarification_question": None, "summary": "取消 E09 周六早班偏好"}),
]


def _constraint_id(prefix: str, item: dict[str, Any]) -> str:
    value = json.dumps(item, ensure_ascii=False, sort_keys=True)
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


def _same_constraint(existing: HardConstraint | SoftConstraint, item: dict[str, Any]) -> bool:
    return (
        existing.kind == item.get("kind")
        and existing.employee_id == item.get("employee_id")
        and existing.day == item.get("day")
        and existing.shift == item.get("shift")
    )


def apply_decision(decision: dict[str, Any], state: ScheduleState) -> Plan:
    if decision.get("action") not in {"generate", "modify", "clarify"}:
        raise PlannerModelError(f"模型返回了非法 action：{decision.get('action')}")
    if decision.get("action") == "clarify":
        raise PlannerNeedsClarification(str(decision.get("clarification_question") or "请补充员工、日期或班次。"))
    hard = list(state.hard_constraints)
    soft = list(state.soft_constraints)
    for item in decision.get("hard_constraints", []):
        if item.get("kind") not in {"unavailable", "must_assign"}:
            raise PlannerModelError(f"模型返回了不允许的硬约束类型：{item.get('kind')}")
        if item.get("operation", "add") not in {"add", "remove"} or not re.fullmatch(r"E\d{2}", str(item.get("employee_id", ""))):
            raise PlannerModelError("模型返回了非法约束操作或员工编号")
        if item.get("day") not in DAYS or item.get("shift") not in {None, "早班", "晚班"}:
            raise PlannerModelError("模型返回了非法日期或班次")
        if item.get("kind") == "must_assign" and item.get("shift") not in {"早班", "晚班"}:
            raise PlannerModelError("must_assign 必须包含明确班次")
        if item.get("operation", "add") == "remove":
            hard = [c for c in hard if not _same_constraint(c, item)]
        elif not any(_same_constraint(c, item) for c in hard):
            hard.append(HardConstraint(_constraint_id("AI-H", item), item["kind"], item.get("employee_id"), item.get("day"), item.get("shift")))
    for item in decision.get("soft_constraints", []):
        if (
            item.get("kind") != "prefer_assignment"
            or item.get("operation", "add") not in {"add", "remove"}
            or not re.fullmatch(r"E\d{2}", str(item.get("employee_id", "")))
            or item.get("day") not in DAYS
            or item.get("shift") not in {"早班", "晚班"}
        ):
            raise PlannerModelError("模型返回了非法软约束")
        if item.get("operation", "add") == "remove":
            soft = [c for c in soft if not _same_constraint(c, item)]
        elif not any(_same_constraint(c, item) for c in soft):
            soft.append(SoftConstraint(_constraint_id("AI-S", item), "prefer_assignment", 10, item.get("employee_id"), item.get("day"), item.get("shift")))
    return Plan(_constraint_id("RUN", {"decision": decision, "schedule": state.schedule_id}), state.schedule_id, tuple(DAYS), tuple(hard), tuple(soft), 3)


class OpenAICompatiblePlannerClient:
    def __init__(self, settings: LLMSettings | None = None):
        self.settings = settings or LLMSettings.from_env()

    def decide(self, query: str, state: ScheduleState) -> dict[str, Any]:
        if not self.settings.configured:
            raise PlannerModelError("未配置大模型")
        messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for user, assistant in FEW_SHOTS:
            messages.extend(({"role": "user", "content": user}, {"role": "assistant", "content": json.dumps(assistant, ensure_ascii=False)}))
        context = {
            "query": query,
            "schedule": {
                "schedule_id": state.schedule_id,
                "week_start": state.week_start,
                "version": state.version,
                "hard_constraints": [asdict(c) for c in state.hard_constraints],
                "soft_constraints": [asdict(c) for c in state.soft_constraints],
            },
            "employees": _employee_context(),
        }
        messages.append({"role": "user", "content": json.dumps(context, ensure_ascii=False)})
        try:
            with httpx.Client(timeout=self.settings.timeout_seconds) as client:
                response = client.post(
                    f"{self.settings.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.api_key}"},
                    json={"model": self.settings.model, "messages": messages, "temperature": 0, "response_format": {"type": "json_object"}},
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            raise PlannerModelError(f"模型调用失败：{exc}") from exc
        if not isinstance(content, str):
            raise PlannerModelError("模型没有返回文本 JSON")
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise PlannerModelError("模型没有返回 JSON 对象")
        try:
            decision = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise PlannerModelError(f"模型 JSON 无法解析：{exc}") from exc
        if not isinstance(decision, dict):
            raise PlannerModelError("模型返回结构不是对象")
        return decision
