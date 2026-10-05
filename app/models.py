from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
SHIFTS = ["早班", "晚班"]
SKILLS = {"店长值守", "饮品制作", "收银", "库存管理"}


@dataclass(frozen=True)
class Employee:
    employee_id: str
    position: str
    skills: frozenset[str]
    available_days: frozenset[str]
    leave_days: frozenset[str] = frozenset()
    preference: str | None = None

    def can_work(self, day: str) -> bool:
        return day in self.available_days and day not in self.leave_days


@dataclass(frozen=True)
class Assignment:
    day: str
    shift: str
    employee_id: str
    locked: bool = False
    source: Literal["solver", "manual"] = "solver"


@dataclass(frozen=True)
class HardConstraint:
    constraint_id: str
    kind: str
    employee_id: str | None = None
    day: str | None = None
    shift: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SoftConstraint:
    constraint_id: str
    kind: str
    weight: int = 1
    employee_id: str | None = None
    day: str | None = None
    shift: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScheduleState:
    schedule_id: str
    week_start: str
    version: int = 1
    assignments: list[Assignment] = field(default_factory=list)
    hard_constraints: list[HardConstraint] = field(default_factory=list)
    soft_constraints: list[SoftConstraint] = field(default_factory=list)
    status: Literal["draft", "published", "archived"] = "draft"

    def locked_assignments(self) -> list[Assignment]:
        return [a for a in self.assignments if a.locked]


@dataclass(frozen=True)
class Plan:
    run_id: str
    schedule_id: str
    scope_days: tuple[str, ...]
    hard_constraints: tuple[HardConstraint, ...]
    soft_constraints: tuple[SoftConstraint, ...]
    max_solutions: int = 3
    objective: str = "balanced_workload"


@dataclass
class Solution:
    assignments: list[Assignment]
    objective_value: int
    explanation_codes: dict[str, list[str]]


@dataclass
class SolveResult:
    status: Literal["feasible", "infeasible", "unknown"]
    solutions: list[Solution] = field(default_factory=list)
    unsat_core: list[str] = field(default_factory=list)
    message: str | None = None

