# 智能排班助手

面向连锁门店的自然语言排班 Agent。用户输入中文需求后，由 LLM Planner Agent 理解意图，OR-Tools CP-SAT 生成满足劳动规则的班表，确定性 Checker 再逐条审计 R-01 至 R-09。服务提供网页、CLI、REST 和飞书入口。

> 大模型只负责把自然语言转换成结构化 Plan，不直接生成班表。最终班次必须通过求解器和规则审计。

## 快速开始

要求 Python 3.12。Windows：

```powershell
cd C:\Users\30989\Documents\Codex\2026-09-28\new-chat
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe run.py
```

打开 `http://127.0.0.1:8000`。也可以运行 `.\start.ps1`。

CLI：

```powershell
.\.venv\Scripts\python.exe -m app --interactive
```

示例输入：

```text
请生成本周排班，尽量照顾员工偏好
E07 周五晚班不能排，请重新排班
尽量让 E09 周六上早班
E07 周五晚班必须安排
第 3 周到第 5 周的排班
第 3 周、第 5 周的排班
```

设置“本周”：

```powershell
$env:SCHEDULE_YEAR = "2026"
$env:SCHEDULE_CURRENT_WEEK = "39"
```

## 架构

```text
自然语言 -> Query Cleaner -> LLM Planner Agent
                              | 当前排班/约束/员工数据
                              v
                         结构化 Plan
                              v
                       Idempotency Store
                              v
                     CP-SAT Solver Executor
                              v
                Deterministic Checker (R-01..R-09)
                              v
                 解释 / 网页 / CLI / 飞书输出
```

核心文件：

| 文件 | 作用 |
|---|---|
| `app/llm.py` | LLM Planner、系统提示词、few-shot、JSON 计划校验 |
| `app/agents.py` | Plan → Execute → Check 流程、未知员工和有限重试 |
| `app/solver.py` | OR-Tools CP-SAT、Top-K 方案、不可行核心 |
| `app/rules.py` | R-01 至 R-09 独立审计、逐班解释 |
| `app/data.py` | 题目提供的 E01-E20 数据 |
| `app/store.py` | SQLite 状态、版本、幂等缓存 |
| `app/api.py` | FastAPI、网页、REST、飞书入口 |
| `app/feishu.py` | 飞书卡片、回复和多维表格适配器 |
| `app/ui.py` | 现场试用网页 |

## 规则与 CP-SAT

排班场景是周一至周日，早班 `09:00-17:00`，晚班 `13:00-21:00`，每人每天最多一班。

以下都是硬约束，直接进入 CP-SAT，违反任何一条都不能输出 `approved`：

- R-01：每个班至少 1 名店长值守；
- R-02：每个班至少 2 名饮品制作；
- R-03：每个班至少 1 名收银；
- R-04：工作日每班至少 4 人，周末每班至少 6 人；
- R-05：每人每周最多 5 个班（40 小时）；
- R-06：任意连续 6 天窗口最多工作 5 天；
- R-07：晚班后不能接次日早班；
- R-08：请假和不可工作日期不得排班；
- R-09：只能使用员工数据已有技能；
- 每人每天最多一个班；
- 已锁定班次不能被增量重排覆盖。

模型变量为 `x[employee, day, shift]`，取 0/1。模型先满足所有硬约束，再最大化员工偏好、用户明确偏好和工作量均衡目标。目标函数不能放松硬约束。

求解器最多返回 3 个方案，并用排除约束要求后续方案与前一个方案存在差异。不可行时使用 assumptions 和删除式 MUS 收缩得到冲突核心。

## Agent 设计

### LLM Planner Agent

Planner 收到当前排班版本、已有约束、E01-E20 员工技能/可用日期/请假/偏好和用户 query，只输出结构化 JSON：

```json
{
  "action": "generate|modify|clarify",
  "force": false,
  "hard_constraints": [],
  "soft_constraints": [],
  "clarification_question": null,
  "summary": "一句话说明计划"
}
```

允许的硬约束只有 `unavailable`、`must_assign`；允许的软约束只有 `prefer_assignment`。服务端还会校验 action、员工 ID、日期、班次和操作类型，非法模型输出不会进入求解器。

若模型未配置或临时失败，默认切换到有限规则解析器，并返回 `planner.mode=deterministic_fallback`。设置 `SCHEDULE_LLM_REQUIRED=true` 可禁止降级。

### Plan-Execute-Check

1. Query Cleaner 统一空格、班次别名和否定表达；
2. Planner 查询状态并输出 Plan；
3. Controller 合并或删除增量约束；
4. Executor 调 CP-SAT，最多生成 3 个方案；
5. Checker 独立审计所有方案的 R-01 至 R-09；
6. 输出逐班技能覆盖、偏好命中、规则状态或无解解释。

没有采用纯 ReAct：排班工具固定、规则固定、求解次数必须受控，Plan-Execute-Check 更适合幂等、审计和重放。ReAct 如需引入，也只放在 Planner 的澄清内部。

## 硬约束与软约束

| 表达 | 类型 | 处理 |
|---|---|---|
| 不能排、不可排、请假 | 硬 | `unavailable`，模型约束 |
| 必须、一定、务必 | 硬 | `must_assign`，模型约束 |
| 尽量、优先、希望 | 软 | `prefer_assignment`，目标函数 |
| 员工早/晚班偏好 | 软 | 目标函数 |
| 人数、技能、工时、休息 | 硬 | 固定业务规则 |

例如“E07 周五晚班不能排，但 E09 周六早班优先安排”会产生一个 E07 硬约束和一个 E09 软约束，两个员工不会串线。

## 幂等与持久化

SQLite 保存排班、版本、约束和请求缓存，默认路径为 `data/schedules.sqlite3`，可用 `SCHEDULE_DB_PATH` 覆盖。请求指纹由排班 ID、清洗后的 query 和当前版本组成。相同状态下重复请求返回：

```json
{"status":"already_generated","replayed":true,"source_run_id":"..."}
```

只有“重新生成/强制重排”或 `force=true` 才绕过缓存。`data/` 已加入 `.gitignore`。生产多实例应替换 PostgreSQL，并增加事务级版本检查和分布式锁。

## API

```text
GET  /health
POST /schedule/run
GET  /schedule/{schedule_id}
POST /feishu/aily/webhook
POST /feishu/events
```

请求：

```json
{
  "schedule_id": "SHOP-DEMO-W01",
  "week_start": "2026-10-05",
  "query": "E07 周五晚班不能排，请重新排班",
  "force": false
}
```

返回包含 `status`、`version`、`run_id`、`replayed`、`planner`、`solutions[].assignments`、`solutions[].explanation` 和 `solutions[].compliance.rules[]`。无解时还包含 `unsat_core_descriptions` 和 `repair_suggestions`。

## 豆包方舟

复制 `.env.example` 为 `.env`，填写：

```dotenv
ARK_API_KEY=你的方舟APIKey
ARK_MODEL=预置模型ID或ep开头的EndpointID
```

或者使用通用变量：

```dotenv
SCHEDULE_LLM_API_KEY=你的密钥
SCHEDULE_LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
SCHEDULE_LLM_MODEL=你的模型或EndpointID
SCHEDULE_LLM_REQUIRED=false
```

重启后检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json -Depth 5
```

`configured=true` 代表配置已读取，实际请求中的 `planner.mode=llm` 代表本次确实调用了模型。不要把 API Key 提交到 Git。

## 飞书

1. 创建企业自建应用并启用机器人；
2. 订阅 `im.message.receive_v1`；
3. 将 `https://你的域名/feishu/events` 配为事件地址；
4. 配置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 和 `FEISHU_EVENT_VERIFICATION_TOKEN`；
5. 服务快速确认事件，后台求解后使用飞书消息回复 API 发送交互卡片。

Aily 可使用 `POST /feishu/aily/webhook`，带 `Authorization: Bearer <FEISHU_AILY_BEARER_TOKEN>`。本地 `127.0.0.1` 只能网页演示；飞书验收需要公网 HTTPS。

Docker：

```powershell
docker build -t scheduling-agent .
docker run --env-file .env -p 8000:8000 scheduling-agent
```

## 技术取舍

### CP-SAT 而不是让 LLM 直接排班

排班是离散组合优化。CP-SAT 可以精确表达技能覆盖、人数、请假、工时、连续工作和相邻班次限制，结果可复现并能诊断不可行。LLM 直接输出表格容易漏规则、重复排班或臆造技能。

### OpenAI-compatible 适配层

Planner 通过统一 Chat Completions 协议接入方舟、DeepSeek 或其他兼容服务，只切换 `base_url`、Key 和模型，不改变 Plan schema、求解器和审计器。

### 保留降级解析器

模型有网络、额度和服务可用性风险。降级解析器支持题目常见表达，且显式返回降级模式，不冒充模型调用。生产可设置 `SCHEDULE_LLM_REQUIRED=true`。

### SQLite 而不是立即引入数据库集群

题目是单店、20 人、单实例演示，SQLite 足够简单可靠；真正上线的多实例版本应迁移 PostgreSQL、加入乐观锁、发布版本和审计日志。

### 不默认写回飞书多维表格

生成、审核、发布是不同状态。默认只生成和展示，避免测试误覆盖线上数据；正式发布需要显式操作、发布版本和写回幂等键。

## 测试与验收

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

当前测试覆盖：真实 CP-SAT 生成、Top-K、硬/软约束分离、must-assign 冲突、未知员工 R-09、R-01 至 R-09 审计、每周 5 班、周次解析、飞书 webhook、幂等缓存和 LLM Planner mock。

推荐现场顺序：

```text
请生成本周排班，尽量照顾员工偏好
E07 周五晚班不能排，请重新排班
E07 周五晚班不能排，请重新排班
E07 周五晚班必须安排
```

## 已知边界

- 多周请求目前按周调用，跨周连续性需要合并为一个 CP-SAT 模型；
- SQLite 适合单实例，多实例需要 PostgreSQL 和锁；
- 复杂自然语言指代应让 Planner 澄清，不能猜测；
- 正式上线需要认证、限流、超时分类、审计日志和飞书发布幂等。
