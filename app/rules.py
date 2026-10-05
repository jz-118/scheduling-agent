from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import Any

from .data import EMPLOYEE_BY_ID
from .models import DAYS, SHIFTS, Assignment, Solution


RULES = {
    "R-01": "每个班至少 1 名具备店长值守资格的员工",
    "R-02": "每个班至少 2 名具备饮品制作技能的员工",
    "R-03": "每个班至少 1 名具备收银技能的员工",
    "R-04": "工作日每班至少 4 人，周末每班至少 6 人",
    "R-05": "每人每周最多 5 个班（40 小时）",
    "R-06": "不得连续工作超过 5 天",
    "R-07": "晚班后次日不得安排早班",
    "R-08": "请假和不可工作日期不得排班",
    "R-09": "技能必须来自员工数据",
}


def _result(rule_id: str, violations: list[str], evidence: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "description": RULES[rule_id],
        "status": "pass" if not violations else "fail",
        "risk_level": "none" if not violations else "high",
        "evidence": evidence,
        "violations": violations,
    }


def audit_assignments(assignments: list[Assignment]) -> dict[str, Any]:
    by_shift: dict[tuple[str, str], list[str]] = {(d, s): [] for d in DAYS for s in SHIFTS}
    by_employee: dict[str, list[Assignment]] = {}
    unknown: list[str] = []
    duplicate_daily: list[str] = []
    seen_daily: set[tuple[str, str]] = set()
    for assignment in assignments:
        employee = EMPLOYEE_BY_ID.get(assignment.employee_id)
        if employee is None:
            unknown.append(f"未知员工 {assignment.employee_id}")
            continue
        by_shift.setdefault((assignment.day, assignment.shift), []).append(assignment.employee_id)
        by_employee.setdefault(assignment.employee_id, []).append(assignment)
        daily = (assignment.employee_id, assignment.day)
        if daily in seen_daily:
            duplicate_daily.append(f"{assignment.employee_id} {assignment.day} 被安排多个班次")
        seen_daily.add(daily)

    v1: list[str] = []
    v2: list[str] = []
    v3: list[str] = []
    v4: list[str] = list(duplicate_daily)
    for day in DAYS:
        for shift in SHIFTS:
            ids = by_shift.get((day, shift), [])
            employees = [EMPLOYEE_BY_ID[eid] for eid in ids if eid in EMPLOYEE_BY_ID]
            if sum("店长值守" in e.skills for e in employees) < 1:
                v1.append(f"{day}{shift} 缺少店长值守")
            if sum("饮品制作" in e.skills for e in employees) < 2:
                v2.append(f"{day}{shift} 饮品制作不足 2 人")
            if sum("收银" in e.skills for e in employees) < 1:
                v3.append(f"{day}{shift} 缺少收银")
            minimum = 6 if day in {"周六", "周日"} else 4
            if len(ids) < minimum:
                v4.append(f"{day}{shift} 仅 {len(ids)} 人，至少需要 {minimum} 人")

    counts = Counter(a.employee_id for a in assignments)
    v5 = [f"{eid} 一周 {count} 个班，超过 5 个" for eid, count in counts.items() if count > 5]
    v6: list[str] = []
    v7: list[str] = []
    v8: list[str] = []
    for eid, rows in by_employee.items():
        employee = EMPLOYEE_BY_ID[eid]
        worked = {DAYS.index(a.day) for a in rows if a.day in DAYS}
        for start in range(2):
            if all(i in worked for i in range(start, start + 6)):
                v6.append(f"{eid} 从{DAYS[start]}起连续工作超过 5 天")
        shifts = {(a.day, a.shift) for a in rows}
        for i in range(6):
            if (DAYS[i], "晚班") in shifts and (DAYS[i + 1], "早班") in shifts:
                v7.append(f"{eid} {DAYS[i]}晚班后接{DAYS[i + 1]}早班")
        for a in rows:
            if not employee.can_work(a.day):
                v8.append(f"{eid} 在不可工作/请假的{a.day}被排班")

    report = [
        _result("R-01", v1, "逐班统计员工数据中的店长值守技能"),
        _result("R-02", v2, "逐班统计员工数据中的饮品制作技能"),
        _result("R-03", v3, "逐班统计员工数据中的收银技能"),
        _result("R-04", v4, "逐班统计总人数，并检查每人每天最多一班"),
        _result("R-05", v5, "按员工统计每周班次数，每班 8 小时"),
        _result("R-06", v6, "检查任意连续 6 天窗口"),
        _result("R-07", v7, "检查相邻日期的晚班到早班组合"),
        _result("R-08", v8, "对照员工可工作日期与请假数据"),
        _result("R-09", unknown, "员工和技能只读取题目提供的员工数据"),
    ]
    violations = [item for rule in report for item in rule["violations"]]
    return {"compliant": not violations, "rules": report, "violations": violations}


def explain_solution(solution: Solution) -> dict[str, Any]:
    shifts: list[dict[str, Any]] = []
    for day in DAYS:
        for shift in SHIFTS:
            rows = [a for a in solution.assignments if a.day == day and a.shift == shift]
            ids = [a.employee_id for a in rows]
            managers = [eid for eid in ids if "店长值守" in EMPLOYEE_BY_ID[eid].skills]
            beverage = [eid for eid in ids if "饮品制作" in EMPLOYEE_BY_ID[eid].skills]
            cashiers = [eid for eid in ids if "收银" in EMPLOYEE_BY_ID[eid].skills]
            preferences = [eid for eid in ids if EMPLOYEE_BY_ID[eid].preference == shift]
            shifts.append({
                "day": day,
                "shift": shift,
                "employees": ids,
                "reason": f"共 {len(ids)} 人；店长值守 {', '.join(managers)}；饮品制作 {', '.join(beverage)}；收银 {', '.join(cashiers)}",
                "matched_preferences": preferences,
            })
    return {"summary": "先满足 R-01 至 R-09 的硬规则，再在可行方案中优化员工偏好和工作量均衡。", "shifts": shifts}


def serialize_solution(solution: Solution) -> dict[str, Any]:
    return {
        "objective_value": solution.objective_value,
        "assignments": [asdict(a) for a in solution.assignments],
        "explanation_codes": solution.explanation_codes,
        "explanation": explain_solution(solution),
        "compliance": audit_assignments(solution.assignments),
    }
