from __future__ import annotations

from collections import defaultdict
from ortools.sat.python import cp_model

from .data import EMPLOYEE_BY_ID
from .models import DAYS, SHIFTS, Assignment, HardConstraint, Plan, Solution, SolveResult, SoftConstraint


class ScheduleSolver:
    """CP-SAT scheduler with bounded diverse solution generation and assumption diagnostics."""

    def solve(self, plan: Plan, locked: list[Assignment]) -> SolveResult:
        solutions: list[Solution] = []
        exclusions: list[dict[tuple[str, str, str], int]] = []
        for _ in range(max(1, min(plan.max_solutions, 10))):
            result, solution = self._solve_once(plan, locked, exclusions)
            if result == "infeasible":
                if not solutions:
                    return SolveResult("infeasible", unsat_core=self._find_unsat_core(plan, locked))
                break
            if result != "feasible" or solution is None:
                break
            solutions.append(solution)
            exclusions.append({(a.day, a.shift, a.employee_id): 1 for a in solution.assignments})
        return SolveResult("feasible" if solutions else "unknown", solutions=solutions)

    def _solve_once(self, plan: Plan, locked: list[Assignment], exclusions):
        model = cp_model.CpModel()
        days = list(plan.scope_days)
        employees = list(EMPLOYEE_BY_ID.values())
        x: dict[tuple[str, str, str], cp_model.IntVar] = {}
        for employee in employees:
            for day in days:
                for shift in SHIFTS:
                    x[(employee.employee_id, day, shift)] = model.NewBoolVar(f"x_{employee.employee_id}_{day}_{shift}")

        for employee in employees:
            for day in days:
                vars_day = [x[(employee.employee_id, day, shift)] for shift in SHIFTS]
                model.Add(sum(vars_day) <= 1)
                if not employee.can_work(day):
                    for var in vars_day:
                        model.Add(var == 0)
            # R-05: each employee may work at most five shifts in the week.
            model.Add(
                sum(
                    x[(employee.employee_id, day, shift)]
                    for day in days
                    for shift in SHIFTS
                )
                <= 5
            )
            for start in range(max(0, len(days) - 5)):
                model.Add(sum(x[(employee.employee_id, days[i], shift)] for i in range(start, start + 6) for shift in SHIFTS) <= 5)
            for i in range(len(days) - 1):
                model.Add(x[(employee.employee_id, days[i], "晚班")] + x[(employee.employee_id, days[i + 1], "早班")] <= 1)

        # Locked assignments are immutable facts in an incremental run.
        for assignment in locked:
            if assignment.day in days:
                model.Add(x[(assignment.employee_id, assignment.day, assignment.shift)] == 1)

        for day in days:
            minimum = 6 if day in {"周六", "周日"} else 4
            for shift in SHIFTS:
                selected = [x[(e.employee_id, day, shift)] for e in employees]
                model.Add(sum(selected) >= minimum)
                model.Add(sum(x[(e.employee_id, day, shift)] for e in employees if "店长值守" in e.skills) >= 1)
                model.Add(sum(x[(e.employee_id, day, shift)] for e in employees if "饮品制作" in e.skills) >= 2)
                model.Add(sum(x[(e.employee_id, day, shift)] for e in employees if "收银" in e.skills) >= 1)

        assumptions: dict[str, cp_model.IntVar] = {}
        for constraint in plan.hard_constraints:
            literal = model.NewBoolVar(f"assume_{constraint.constraint_id}")
            assumptions[constraint.constraint_id] = literal
            for key, var in x.items():
                employee_id, day, shift = key
                if constraint.kind == "unavailable" and employee_id == constraint.employee_id and day == constraint.day and (constraint.shift is None or shift == constraint.shift):
                    model.Add(var == 0).OnlyEnforceIf(literal)
                elif constraint.kind == "must_assign" and employee_id == constraint.employee_id and day == constraint.day and shift == constraint.shift:
                    model.Add(var == 1).OnlyEnforceIf(literal)
            model.AddAssumption(literal)

        for previous in exclusions:
            differences = []
            for key, var in x.items():
                employee_id, day, shift = key
                was_selected = previous.get((day, shift, employee_id), 0)
                differences.append(var if was_selected == 0 else var.Not())
            model.Add(sum(differences) >= 2)

        preference_terms = []
        for employee in employees:
            for day in days:
                for shift in SHIFTS:
                    if employee.preference == shift:
                        preference_terms.append(5 * x[(employee.employee_id, day, shift)])
        for constraint in plan.soft_constraints:
            if constraint.kind == "prefer_assignment":
                preference_terms.append(constraint.weight * x[(constraint.employee_id, constraint.day, constraint.shift)])
        workload = []
        for employee in employees:
            count = sum(x[(employee.employee_id, day, shift)] for day in days for shift in SHIFTS)
            workload.append(count)
        max_count = model.NewIntVar(0, len(days), "max_count")
        min_count = model.NewIntVar(0, len(days), "min_count")
        for count in workload:
            model.Add(count <= max_count)
            model.Add(count >= min_count)
        model.Maximize(sum(preference_terms) - 2 * (max_count - min_count))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 8
        solver.parameters.num_search_workers = 8
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return "infeasible" if status == cp_model.INFEASIBLE else "unknown", None
        assignments: list[Assignment] = []
        codes: dict[str, list[str]] = defaultdict(list)
        for employee in employees:
            for day in days:
                for shift in SHIFTS:
                    if solver.Value(x[(employee.employee_id, day, shift)]):
                        assignments.append(Assignment(day, shift, employee.employee_id))
                        if employee.preference == shift:
                            codes[employee.employee_id].append("MATCH_PREFERENCE")
                        if "店长值守" in employee.skills:
                            codes[employee.employee_id].append("COVER_MANAGER")
        return "feasible", Solution(assignments, int(solver.ObjectiveValue()), dict(codes))

    def _find_unsat_core(self, plan: Plan, locked: list[Assignment]) -> list[str]:
        if not plan.hard_constraints:
            return ["BASE_RULES_OR_LOCKED_ASSIGNMENTS"]
        active = list(plan.hard_constraints)
        # Deletion-based MUS shrinking: subset-minimal, not minimum-cardinality.
        for candidate in list(active):
            trial = [c for c in active if c.constraint_id != candidate.constraint_id]
            trial_plan = Plan(plan.run_id, plan.schedule_id, plan.scope_days, tuple(trial), plan.soft_constraints, 1, plan.objective)
            result = self._solve_once(trial_plan, locked, [])[0]
            if result == "infeasible":
                active = trial
        return [c.constraint_id for c in active] or ["BASE_RULES_OR_LOCKED_ASSIGNMENTS"]

