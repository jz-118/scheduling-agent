from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path

from .agents import SchedulingOrchestrator
from .models import ScheduleState
from .rules import audit_assignments


def _json_safe(value):
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json_safe(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _load_request(path: str | None) -> dict:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    raw = sys.stdin.read().strip()
    if not raw:
        raise SystemExit("请输入自然语言需求，或使用 --input JSON 文件")
    return json.loads(raw)


def _current_week() -> tuple[int, int]:
    today = dt.date.today()
    year = int(os.getenv("SCHEDULE_YEAR", str(today.isocalendar().year)))
    week = int(os.getenv("SCHEDULE_CURRENT_WEEK", str(today.isocalendar().week)))
    return year, week


def _week_start(year: int, week: int) -> str:
    return dt.date.fromisocalendar(year, week, 1).isoformat()


def parse_week_range(query: str, year: int, current_week: int) -> list[tuple[int, int]]:
    """Return ISO weeks mentioned by the user, defaulting to the configured current week."""
    range_match = re.search(r"第\s*(\d+)\s*周\s*(?:到|至|-|~)\s*第?\s*(\d+)\s*周", query)
    if range_match:
        first, last = map(int, range_match.groups())
        if first > last:
            first, last = last, first
        return [(year, week) for week in range(first, last + 1)]
    explicit = re.search(r"第\s*(\d+)\s*周", query)
    all_explicit = [int(value) for value in re.findall(r"第\s*(\d+)\s*周", query)]
    if len(all_explicit) > 1:
        return [(year, week) for week in dict.fromkeys(all_explicit)]
    if explicit:
        return [(year, int(explicit.group(1)))]
    if "下周" in query:
        next_date = dt.date.fromisocalendar(year, current_week, 1) + dt.timedelta(days=7)
        iso = next_date.isocalendar()
        return [(iso.year, iso.week)]
    return [(year, current_week)]


def _print_result(result: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(_json_safe(result), ensure_ascii=False, indent=2))
        return
    print(f"状态: {result['status']}")
    print(f"尝试次数: {result.get('attempts', 0)}")
    if result["status"] == "approved":
        for index, solution in enumerate(result["solutions"], start=1):
            print(f"\n方案 {index} | 目标分: {solution.objective_value}")
            for day in ("周一", "周二", "周三", "周四", "周五", "周六", "周日"):
                rows = [a for a in solution.assignments if a.day == day]
                if rows:
                    early = ", ".join(a.employee_id for a in rows if a.shift == "早班")
                    late = ", ".join(a.employee_id for a in rows if a.shift == "晚班")
                    print(f"{day}: 早班 [{early}]  晚班 [{late}]")
            audit = audit_assignments(solution.assignments)
            print("规则校验:", "R-01 至 R-09 全部通过" if audit["compliant"] else "；".join(audit["violations"]))
    elif result["status"] == "infeasible":
        print(result.get("user_message", "当前条件无法生成排班，因为部分排班要求互相矛盾。"))
        print("处理建议:", "；".join(result.get("repair_suggestions", [])))
    else:
        print(result.get("reason") or "；".join(result.get("violations", [])))


def run_query(query: str, schedule_id: str, year: int, current_week: int, json_output: bool = False, states=None, orchestrator=None) -> None:
    weeks = parse_week_range(query, year, current_week)
    states = states if states is not None else {}
    orchestrator = orchestrator or SchedulingOrchestrator()
    for week_year, week in weeks:
        weekly_id = f"{schedule_id}-W{week:02d}"
        state = states.setdefault(weekly_id, ScheduleState(weekly_id, _week_start(week_year, week)))
        result = orchestrator.run(query, state)
        if result.get("status") == "approved":
            state.assignments = result["solutions"][0].assignments
            plan = result.get("plan")
            if plan:
                state.hard_constraints = list(plan.hard_constraints)
                state.soft_constraints = list(plan.soft_constraints)
        if not json_output:
            print(f"\n===== {week_year} 第 {week} 周（{state.week_start}） =====")
        _print_result(result, json_output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="智能排班助手 CLI")
    parser.add_argument("query", nargs="?", help="自然语言排班需求")
    parser.add_argument("--schedule-id", default="CLI-DEMO-W01")
    parser.add_argument("--week-start", default="2026-09-28")
    parser.add_argument("--input", help="JSON 请求文件，字段：query/schedule_id/week_start")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--interactive", action="store_true", help="启动交互式排班终端")
    parser.add_argument("--current-week", type=int, help="覆盖 SCHEDULE_CURRENT_WEEK")
    args = parser.parse_args(argv)

    year, current_week = _current_week()
    if args.current_week:
        current_week = args.current_week

    if args.interactive or (not args.input and not args.query):
        print(f"智能排班助手：当前按 {year} 年第 {current_week} 周处理。输入 exit 或 quit 退出。")
        interactive_states = {}
        interactive_orchestrator = SchedulingOrchestrator()
        while True:
            try:
                query = input("排班> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if query.lower() in {"exit", "quit", "q", "退出"}:
                return 0
            if query:
                run_query(query, args.schedule_id, year, current_week, args.json, interactive_states, interactive_orchestrator)
        

    if args.input:
        request = _load_request(args.input)
        query = request["query"]
        schedule_id = request.get("schedule_id", args.schedule_id)
        week_start = request.get("week_start", args.week_start)
    elif args.query:
        query = args.query
        schedule_id = args.schedule_id
        week_start = args.week_start
    else:
        request = _load_request(None)
        query = request["query"]
        schedule_id = request.get("schedule_id", args.schedule_id)
        week_start = request.get("week_start", args.week_start)

    run_query(query, schedule_id, year, current_week, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
