from collections import Counter

from app.agents import SchedulingOrchestrator
from app.models import Assignment, ScheduleState
from app.rules import audit_assignments


def test_generated_schedule_passes_all_nine_rules_and_weekly_limit():
    result = SchedulingOrchestrator().run("请生成本周排班", ScheduleState("AUDIT", "2026-09-28"))
    assert result["status"] == "approved"
    for solution in result["solutions"]:
        audit = audit_assignments(solution.assignments)
        assert audit["compliant"] is True
        assert all(item["status"] == "pass" for item in audit["rules"])
        assert max(Counter(a.employee_id for a in solution.assignments).values()) <= 5


def test_rule_audit_reports_unknown_employee_and_unavailable_day():
    audit = audit_assignments([
        Assignment("周三", "早班", "E01"),
        Assignment("周一", "早班", "E99"),
    ])
    assert audit["compliant"] is False
    failures = {item["rule_id"] for item in audit["rules"] if item["status"] == "fail"}
    assert "R-08" in failures
    assert "R-09" in failures
