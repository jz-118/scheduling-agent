from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from .data import EMPLOYEE_BY_ID
from .llm import LLMSettings, OpenAICompatiblePlannerClient, PlannerModelError, PlannerNeedsClarification, apply_decision
from .models import DAYS, Assignment, HardConstraint, Plan, ScheduleState, SoftConstraint, Solution
from .rules import audit_assignments
from .solver import ScheduleSolver


@dataclass
class CheckResult:
    approved: bool
    repairable: bool
    violations: list[str]


def clean_query(query: str) -> str:
    """Normalize harmless surface variations before the planner parses intent."""
    normalized = re.sub(r"\s+", " ", query.strip())
    normalized = normalized.replace("早班次", "早班").replace("晚班次", "晚班")
    normalized = normalized.replace("不能上班", "不能排").replace("不可以上班", "不能排")
    return normalized


def describe_constraint(constraint: HardConstraint) -> str:
    if constraint.kind == "unavailable":
        target = f"{constraint.employee_id} {constraint.day}"
        return f"{target}{constraint.shift or '所有班次'}不可排班"
    if constraint.kind == "must_assign":
        return f"必须安排 {constraint.employee_id} 上 {constraint.day}{constraint.shift}"
    return f"未识别约束 {constraint.constraint_id}（类型：{constraint.kind}）"


def user_conflict_message(descriptions: list[str]) -> str:
    if len(descriptions) == 2 and "不可排班" in descriptions[0] and "必须安排" in descriptions[1]:
        return f"当前条件无法生成排班，因为“{descriptions[0]}”和“{descriptions[1]}”互相矛盾。"
    if len(descriptions) == 2 and "必须安排" in descriptions[0] and "不可排班" in descriptions[1]:
        return f"当前条件无法生成排班，因为“{descriptions[0]}”和“{descriptions[1]}”互相矛盾。"
    return "当前条件无法生成排班，因为部分排班要求互相矛盾。"


class PlannerAgent:
    """Deterministic fallback planner for the supported demo expressions."""

    def plan(self, query: str, state: ScheduleState) -> Plan:
        query = clean_query(query)
        hard: list[HardConstraint] = []
        soft: list[SoftConstraint] = []
        clauses = re.split(r"[，,；;。]|但(?=\s*E\d{2})|同时(?=\s*E\d{2})|另外(?=\s*E\d{2})", query, flags=re.I)
        for clause in clauses:
            employee_ids = list(dict.fromkeys(re.findall(r"E\d{2}", clause.upper())))
            if not employee_ids:
                continue
            day = next((day for day in DAYS if day in clause), None)
            shift = next((shift for shift in ("早班", "晚班") if shift in clause), None)
            for employee_id in employee_ids:
                if any(token in clause for token in ("不能", "不可", "请假")) and day:
                    hard.append(HardConstraint(f"Q-{uuid.uuid4().hex[:8]}", "unavailable", employee_id, day, shift))
                    continue
                if any(token in clause for token in ("必须", "一定", "务必")) and day and shift:
                    hard.append(HardConstraint(f"Q-{uuid.uuid4().hex[:8]}", "must_assign", employee_id, day, shift))
                    continue
                explicit_preference = any(token in clause for token in ("优先", "尽量让", "希望"))
                if explicit_preference and day and shift:
                    soft.append(SoftConstraint(f"Q-{uuid.uuid4().hex[:8]}", "prefer_assignment", 10, employee_id, day, shift))
        scope = tuple(DAYS)
        mentioned_days = tuple(day for day in DAYS if day in query)
        if mentioned_days:
            # Keep the whole week in the model so R-05/R-06/R-07 remain correct.
            scope = tuple(DAYS)
        return Plan(str(uuid.uuid4()), state.schedule_id, scope, tuple(state.hard_constraints + hard), tuple(state.soft_constraints + soft), 3)


class LLMPlannerAgent:
    """Model-backed planner with a deterministic fallback and an auditable trace."""

    def __init__(self, client: OpenAICompatiblePlannerClient | None = None, fallback: PlannerAgent | None = None):
        self.client = client or OpenAICompatiblePlannerClient()
        self.fallback = fallback or PlannerAgent()
        self.last_trace: dict = {}

    def status(self) -> dict:
        settings = self.client.settings
        return {
            "agent": "LLMPlannerAgent",
            "configured": settings.configured,
            "model": settings.model or None,
            "base_url": settings.base_url or None,
            "fallback": "PlannerAgent",
        }

    def plan(self, query: str, state: ScheduleState) -> Plan:
        settings = self.client.settings
        if not settings.configured:
            self.last_trace = {**self.status(), "mode": "deterministic_fallback", "reason": "未配置模型 API"}
            return self.fallback.plan(query, state)
        try:
            decision = self.client.decide(query, state)
            plan = apply_decision(decision, state)
            self.last_trace = {**self.status(), "mode": "llm", "summary": decision.get("summary"), "action": decision.get("action")}
            return plan
        except PlannerNeedsClarification:
            self.last_trace = {**self.status(), "mode": "llm", "action": "clarify"}
            raise
        except PlannerModelError as exc:
            if settings.required:
                self.last_trace = {**self.status(), "mode": "llm_error", "error": str(exc)}
                raise
            self.last_trace = {**self.status(), "mode": "deterministic_fallback", "reason": str(exc)}
            return self.fallback.plan(query, state)


class ExecutorAgent:
    def __init__(self, solver: ScheduleSolver):
        self.solver = solver

    def execute(self, plan: Plan, state: ScheduleState):
        return self.solver.solve(plan, state.locked_assignments())


class CheckerAgent:
    def review(self, solutions: list[Solution], state: ScheduleState) -> CheckResult:
        if not solutions:
            return CheckResult(False, False, ["没有可检查的排班方案"])
        violations: list[str] = []
        for index, solution in enumerate(solutions, start=1):
            audit = audit_assignments(solution.assignments)
            violations.extend(f"方案 {index}: {item}" for item in audit["violations"])
            for assignment in state.locked_assignments():
                if assignment not in solution.assignments:
                    violations.append(f"方案 {index}: 已锁定安排被修改: {assignment.employee_id} {assignment.day} {assignment.shift}")
        return CheckResult(not violations, bool(violations), violations)


class SchedulingOrchestrator:
    """Plan -> Execute -> Check state machine with bounded repair attempts."""

    def __init__(self):
        self.planner = LLMPlannerAgent()
        self.executor = ExecutorAgent(ScheduleSolver())
        self.checker = CheckerAgent()

    def run(self, query: str, state: ScheduleState) -> dict:
        try:
            plan = self.planner.plan(query, state)
        except PlannerNeedsClarification as exc:
            return {"status": "needs_clarification", "reason": str(exc), "attempts": 0, "planner": self.planner.last_trace}
        except PlannerModelError as exc:
            return {"status": "blocked", "reason": str(exc), "attempts": 0, "planner": self.planner.last_trace}
        trace = self.planner.last_trace
        unknown = sorted({
            c.employee_id
            for c in (*plan.hard_constraints, *plan.soft_constraints)
            if c.employee_id and c.employee_id not in EMPLOYEE_BY_ID
        })
        if unknown:
            return {
                "status": "blocked",
                "reason": f"员工数据中不存在：{', '.join(unknown)}。依据 R-09，Agent 不得自行补充员工或技能。",
                "violations": ["R-09"],
                "attempts": 0,
                "planner": trace,
            }
        seen_plans: set[str] = set()
        for attempt in range(3):
            plan_key = repr(plan)
            if plan_key in seen_plans:
                return {"status": "blocked", "reason": "检测到计划未变化，已停止自动循环", "attempts": attempt, "planner": trace}
            seen_plans.add(plan_key)
            result = self.executor.execute(plan, state)
            if result.status == "infeasible":
                constraints_by_id = {constraint.constraint_id: constraint for constraint in plan.hard_constraints}
                descriptions = [
                    describe_constraint(constraints_by_id[constraint_id])
                    for constraint_id in result.unsat_core
                    if constraint_id in constraints_by_id
                ]
                if not descriptions:
                    descriptions = ["基础劳动规则、已锁定排班或员工可用性之间存在冲突"]
                return {
                    "status": "infeasible",
                    "attempts": attempt + 1,
                    "unsat_core": result.unsat_core,
                    "unsat_core_descriptions": descriptions,
                    "user_message": user_conflict_message(descriptions),
                    "repair_suggestions": ["放宽其中一条硬约束，或允许修改相关已锁定班次后重新求解"],
                    "planner": trace,
                }
            check = self.checker.review(result.solutions, state)
            if check.approved:
                return {"status": "approved", "attempts": attempt + 1, "plan": plan, "solutions": result.solutions, "planner": trace}
            return {"status": "blocked", "attempts": attempt + 1, "violations": check.violations, "planner": trace}
        return {"status": "blocked", "reason": "超过最大修复次数", "attempts": 3, "planner": trace}
