# 智能排班助手提交说明

## 1. Agent 访问入口

本地启动：双击或在 PowerShell 执行 `./start.ps1`，打开 `http://127.0.0.1:8000`。

飞书入口：将本服务部署到公网 HTTPS 后，把 `POST /feishu/events` 配置为飞书事件订阅地址；也可在飞书 Aily 使用 `POST /feishu/aily/webhook` 作为同步自定义触发器。

## 2. 支持的输入

支持中文自然语言文本，例如：

- `请生成本周排班，尽量照顾员工偏好`
- `E07 周五晚班不能排，请重新排班`
- `尽量让 E09 周六上早班`
- `E07 周五晚班必须安排`

CLI 还支持 `本周`、`下周`、`第 N 周`、`第 N 周到第 M 周` 和离散周次。

## 3. 规则使用方式

LLM Planner Agent 查询当前排班和约束后，将自然语言编译为 `unavailable`、`must_assign` 和 `prefer_assignment` 等结构化计划。模型只负责意图理解，不直接生成班表。R-01 至 R-09 的硬规则由 OR-Tools CP-SAT 模型执行，不依赖大模型常识。求解后，独立规则审计器再次逐条检查所有结果；任何硬规则失败都会阻止输出。

软约束仅参与目标函数，包括员工早晚班偏好、用户明确提出的“优先/尽量”以及工作量均衡。软约束不会覆盖请假、技能、人数、工时或休息规则。

## 4. 输出格式

网页和飞书输出包含 7 天早晚班排班。API 额外返回：

- `status`、`version`、`run_id`、`replayed`
- `planner`：本次使用的 Agent、模型、动作、摘要或降级原因
- `solutions[].assignments`
- `solutions[].explanation`：安排原则和逐班技能覆盖解释
- `solutions[].compliance.rules[]`：规则编号、是否通过、风险等级、证据和违规详情
- 无解时返回 `unsat_core_descriptions` 和 `repair_suggestions`
- 员工不存在时依据 R-09 明确拒绝，不会自行补充技能

## 5. 自测样例

输入：`E07 周五晚班不能排，请重新排班，尽量照顾员工偏好`

预期：状态为 `approved`；三个候选方案均不包含 E07 周五晚班；R-01 至 R-09 全部为 `pass`。相同请求再次提交返回 `already_generated` 和 `replayed=true`，不会重复求解。

自动测试：`./.venv/Scripts/python.exe -m pytest -q`。

## 飞书配置

1. 在飞书开放平台创建企业自建应用并启用机器人。
2. 申请接收消息及以应用身份发送消息所需权限，订阅 `im.message.receive_v1`。
3. 将 `.env.example` 复制为 `.env`，填写 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 和 `FEISHU_EVENT_VERIFICATION_TOKEN`。
4. 部署到公网 HTTPS，将事件订阅 URL 设为 `https://你的域名/feishu/events`。
5. 向机器人发送自然语言排班需求；服务立即确认事件，在后台求解后用交互卡片回复。

飞书服务端采用官方消息 API：`POST /open-apis/im/v1/messages/{message_id}/reply`。本地 `127.0.0.1` 无法被飞书服务器访问，现场仅本机演示时请使用网页入口。

## Planner Agent 配置

在 `.env` 填写 `SCHEDULE_LLM_API_KEY`、`SCHEDULE_LLM_BASE_URL` 和 `SCHEDULE_LLM_MODEL`，即可连接豆包方舟或其他 OpenAI-compatible 模型。未配置模型时网页会明确显示“规则解析降级模式”，不会冒充模型调用成功。模型只产生结构化计划，求解器和审计器始终是最终裁决者。
