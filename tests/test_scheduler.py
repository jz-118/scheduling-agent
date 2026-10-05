from app.agents import SchedulingOrchestrator
from app.agents import LLMPlannerAgent, PlannerAgent, describe_constraint, user_conflict_message
from app.llm import LLMSettings
from app.models import HardConstraint, Plan, ScheduleState
from app.solver import ScheduleSolver


def test_generates_multiple_checked_solutions():
    result = SchedulingOrchestrator().run("请生成本周排班，尽量照顾员工偏好", ScheduleState("TEST", "2026-09-28"))
    assert result["status"] == "approved"
    assert len(result["solutions"]) >= 1
    assert len(result["solutions"]) <= 3
    assert result["attempts"] == 1


def test_hard_constraint_is_applied():
    result = SchedulingOrchestrator().run("E07 周五晚班不能排，请重新排班", ScheduleState("TEST", "2026-09-28"))
    assert result["status"] == "approved"
    for solution in result["solutions"]:
        assert not any(a.employee_id == "E07" and a.day == "周五" and a.shift == "晚班" for a in solution.assignments)


def test_infeasible_core_has_human_description():
    constraints = (
        HardConstraint("C-OFF", "unavailable", "E07", "周五", "晚班"),
        HardConstraint("C-MUST", "must_assign", "E07", "周五", "晚班"),
    )
    plan = Plan("UNSAT", "UNSAT", ("周一", "周二", "周三", "周四", "周五", "周六", "周日"), constraints, (), 1)
    result = SchedulingOrchestrator().run("E07 周五晚班不能排", ScheduleState("UNSAT", "2026-09-28"))
    direct = ScheduleSolver().solve(plan, [])
    assert direct.unsat_core == ["C-OFF", "C-MUST"]
    assert describe_constraint(constraints[0]) == "E07 周五晚班不可排班"
    assert "互相矛盾" in user_conflict_message([
        describe_constraint(constraints[0]),
        describe_constraint(constraints[1]),
    ])


def test_planner_keeps_hard_and_soft_constraints_separate_by_clause():
    plan = PlannerAgent().plan(
        "E07 周五晚班不能排，但 E09 周六早班优先安排",
        ScheduleState("TEST", "2026-09-28"),
    )
    assert [(c.kind, c.employee_id, c.day, c.shift) for c in plan.hard_constraints] == [
        ("unavailable", "E07", "周五", "晚班")
    ]
    assert [(c.kind, c.employee_id, c.day, c.shift) for c in plan.soft_constraints] == [
        ("prefer_assignment", "E09", "周六", "早班")
    ]


def test_explicit_must_assign_is_hard_and_unknown_employee_is_blocked():
    plan = PlannerAgent().plan("E07 周五晚班必须安排", ScheduleState("TEST", "2026-09-28"))
    assert plan.hard_constraints[0].kind == "must_assign"
    result = SchedulingOrchestrator().run("E99 周五晚班必须安排", ScheduleState("TEST", "2026-09-28"))
    assert result["status"] == "blocked"
    assert result["violations"] == ["R-09"]


def test_llm_planner_applies_structured_decision_and_reports_trace():
    class FakeClient:
        settings = LLMSettings("test-key", "https://example.invalid/v1", "test-model")

        def decide(self, query, state):
            return {
                "action": "modify",
                "force": False,
                "hard_constraints": [],
                "soft_constraints": [{
                    "operation": "add", "kind": "prefer_assignment",
                    "employee_id": "E09", "day": "周六", "shift": "早班",
                }],
                "clarification_question": None,
                "summary": "新增 E09 周六早班偏好",
            }

    planner = LLMPlannerAgent(client=FakeClient())
    plan = planner.plan("让 E09 周六早点来", ScheduleState("TEST", "2026-09-28"))
    assert plan.soft_constraints[0].employee_id == "E09"
    assert planner.last_trace["mode"] == "llm"
    assert planner.last_trace["model"] == "test-model"
