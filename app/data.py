from __future__ import annotations

from .models import DAYS, Employee


def _days(*values: str) -> frozenset[str]:
    return frozenset(values)


EMPLOYEES = [
    Employee("E01", "店长", frozenset({"店长值守", "饮品制作", "收银", "库存管理"}), _days(*DAYS), _days("周三"), "早班"),
    Employee("E02", "副店长", frozenset({"店长值守", "饮品制作", "收银"}), _days(*DAYS), preference="晚班"),
    Employee("E03", "值班主管", frozenset({"店长值守", "饮品制作", "收银"}), _days("周一", "周二", "周三", "周四", "周五")),
    Employee("E04", "值班主管", frozenset({"店长值守", "饮品制作", "库存管理"}), _days("周三", "周四", "周五", "周六", "周日"), preference="早班"),
    Employee("E05", "高级店员", frozenset({"店长值守", "饮品制作", "收银"}), _days("周五", "周六", "周日"), preference="晚班"),
    Employee("E06", "店员", frozenset({"饮品制作", "收银"}), _days(*DAYS), _days("周二"), "早班"),
    Employee("E07", "店员", frozenset({"饮品制作", "收银"}), _days(*DAYS), preference="晚班"),
    Employee("E08", "店员", frozenset({"饮品制作", "收银", "库存管理"}), _days("周一", "周二", "周三", "周四", "周五", "周六")),
    Employee("E09", "店员", frozenset({"饮品制作"}), _days("周一", "周三", "周五", "周六", "周日"), preference="早班"),
    Employee("E10", "店员", frozenset({"饮品制作", "收银"}), _days("周二", "周三", "周四", "周五", "周六", "周日")),
    Employee("E11", "店员", frozenset({"收银", "库存管理"}), _days(*DAYS), preference="晚班"),
    Employee("E12", "店员", frozenset({"饮品制作", "收银"}), _days("周一", "周二", "周三", "周四", "周五"), preference="早班"),
    Employee("E13", "兼职", frozenset({"饮品制作"}), _days("周六", "周日"), preference="早班"),
    Employee("E14", "兼职", frozenset({"饮品制作", "收银"}), _days("周六", "周日"), preference="晚班"),
    Employee("E15", "兼职", frozenset({"收银"}), _days("周五", "周六", "周日")),
    Employee("E16", "兼职", frozenset({"饮品制作"}), _days("周三", "周四", "周六", "周日")),
    Employee("E17", "店员", frozenset({"饮品制作", "收银", "库存管理"}), _days(*DAYS), _days("周一")),
    Employee("E18", "店员", frozenset({"饮品制作", "收银"}), _days("周一", "周二", "周三", "周四", "周五"), preference="晚班"),
    Employee("E19", "兼职", frozenset({"饮品制作", "收银"}), _days("周六", "周日")),
    Employee("E20", "店员", frozenset({"饮品制作", "库存管理"}), _days("周二", "周三", "周五", "周六", "周日"), _days("周四"), "早班"),
]

EMPLOYEE_BY_ID = {employee.employee_id: employee for employee in EMPLOYEES}

