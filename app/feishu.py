from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx


def _load_local_env() -> None:
    """Load a simple local .env without adding a runtime dependency."""
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
class FeishuSettings:
    aily_bearer_token: str = os.getenv("FEISHU_AILY_BEARER_TOKEN", "")
    event_verification_token: str = os.getenv("FEISHU_EVENT_VERIFICATION_TOKEN", "")
    app_id: str = os.getenv("FEISHU_APP_ID", "")
    app_secret: str = os.getenv("FEISHU_APP_SECRET", "")
    bitable_app_token: str = os.getenv("FEISHU_BITABLE_APP_TOKEN", "")
    bitable_table_id: str = os.getenv("FEISHU_BITABLE_TABLE_ID", "")


def verify_bearer(authorization: str | None, expected: str) -> bool:
    if not expected:
        return True
    return authorization == f"Bearer {expected}"


def extract_aily_query(payload: dict[str, Any]) -> str:
    data = payload.get("data", payload)
    if isinstance(data, str):
        return data
    for key in ("query", "text", "message", "input", "request"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(data, ensure_ascii=False)


def extract_event_query(payload: dict[str, Any]) -> tuple[str, str | None]:
    event = payload.get("event", {})
    message = event.get("message", {}) if isinstance(event, dict) else {}
    content = message.get("content", "")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            pass
    if isinstance(content, dict):
        text = content.get("text", "")
    else:
        text = str(content)
    return text.strip(), message.get("message_id")


def result_text(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status in {"approved", "already_generated"}:
        solutions = result.get("solutions", [])
        prefix = "已找到相同请求，复用原排班。" if status == "already_generated" else "排班已生成并通过 R-01 至 R-09 校验。"
        lines = [prefix]
        if solutions:
            solution = solutions[0]
            rows = solution.assignments if hasattr(solution, "assignments") else solution.get("assignments", [])
            for day in ("周一", "周二", "周三", "周四", "周五", "周六", "周日"):
                def value(row, key):
                    return getattr(row, key) if hasattr(row, key) else row.get(key)
                early = [value(row, "employee_id") for row in rows if value(row, "day") == day and value(row, "shift") == "早班"]
                late = [value(row, "employee_id") for row in rows if value(row, "day") == day and value(row, "shift") == "晚班"]
                lines.append(f"**{day}** 早班：{', '.join(early)}；晚班：{', '.join(late)}")
            lines.append("安排原则：先满足全部硬规则，再优化员工偏好和工作量均衡。")
        return "\n".join(lines)
    if status == "infeasible":
        detail = result.get("user_message", "当前条件无法生成排班，因为部分排班要求互相矛盾。")
        suggestions = "；".join(result.get("repair_suggestions", []))
        return f"{detail}处理建议：{suggestions}"
    if status == "blocked":
        return "排班流程已停止：" + str(result.get("reason") or "；".join(result.get("violations", [])))
    return f"排班状态：{status}"


def make_card(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "智能排班助手"}},
        "body": {"elements": [{"tag": "markdown", "content": result_text(result)}]},
    }


class FeishuBitableClient:
    """Optional write-back adapter. It is inactive until app credentials are configured."""

    def __init__(self, settings: FeishuSettings):
        self.settings = settings

    async def tenant_access_token(self) -> str:
        if not self.settings.app_id or not self.settings.app_secret:
            raise RuntimeError("FEISHU_APP_ID/FEISHU_APP_SECRET are required for Base write-back")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.settings.app_id, "app_secret": self.settings.app_secret},
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                raise RuntimeError(body.get("msg", "Feishu token request failed"))
            return body["tenant_access_token"]

    async def append_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.settings.bitable_app_token or not self.settings.bitable_table_id:
            raise RuntimeError("FEISHU_BITABLE_APP_TOKEN/FEISHU_BITABLE_TABLE_ID are required")
        token = await self.tenant_access_token()
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.settings.bitable_app_token}/tables/{self.settings.bitable_table_id}/records/batch_create",
                headers={"Authorization": f"Bearer {token}"},
                json={"records": [{"fields": record} for record in records]},
            )
            response.raise_for_status()
            return response.json()

    async def reply_message(self, message_id: str, card: dict[str, Any]) -> dict[str, Any]:
        """Reply to a received message as the configured Feishu bot."""
        token = await self.tenant_access_token()
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
                headers={"Authorization": f"Bearer {token}"},
                json={"msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                raise RuntimeError(body.get("msg", "Feishu reply failed"))
            return body
