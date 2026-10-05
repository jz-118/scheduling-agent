from app.cli import parse_week_range


def test_week_range_parser_supports_single_and_multiple_weeks():
    assert parse_week_range("第 3 周的排班", 2026, 39) == [(2026, 3)]
    assert parse_week_range("第3周到第5周的排班", 2026, 39) == [(2026, 3), (2026, 4), (2026, 5)]
    assert parse_week_range("第3周、第5周的排班", 2026, 39) == [(2026, 3), (2026, 5)]


def test_week_range_parser_defaults_to_configured_current_week():
    assert parse_week_range("请生成本周排班", 2026, 39) == [(2026, 39)]
