# live-chat 协议

服务只监听 loopback。默认运行状态目录为Windows `%LOCALAPPDATA%\agent-live-chat`、macOS `~/Library/Application Support/agent-live-chat`、Linux `$XDG_STATE_HOME/agent-live-chat`（回退到`~/.local/state/agent-live-chat`）。可通过 `LIVE_CHAT_STATE_DIR` 覆盖，服务 URL 可通过 `LIVE_CHAT_URL` 或 CLI `--url` 覆盖。

## 状态模型

当前版本保持 HTTP `protocol_version=1`，将会话快照升级为 `session_schema_version=2`，事件升级为 `event_protocol_version=2`。读取时兼容 Schema 1 和事件 v1；写入和导出使用 v2。旧客户端继续读写活动会话；新客户端必须从 health 的版本与 `features` 判断能力，不得仅根据应用版本猜测。

- `instance_id`：单次服务进程标识。变化时客户端完整重拉。
- `app_version`：应用发行版本；增量加入health、instance和CLI status输出。
- `epoch`：reset 或 seed 时递增。消息唯一键为 `epoch:id`。
- `revision`：每次成功写操作递增。
- `total`：当前 epoch 的消息数量。
- `session`：目标、交付物、完成条件、模型策略、角色运行配置、轮次、阶段和结束状态。
- `workflow`：受控策略、批准状态，以及轮次、角色、重试和墙钟时间预算。
- `pending_decision`：当前唯一待处理人工决策；没有时为 null。
- `run`：运行 ID、参与者生命周期和每轮精简摘要。
- `result`：最终摘要、逐项完成条件、证据引用、残留分歧和下一步。
- `participants`：有序参与者名册，包含尚未发言的成员；消息发送者和 typing 成员可自动追加。
- `typing`：正在输入的成员映射。
- `session_id`：稳定的32位小写UUID十六进制会话标识。

状态目录新增`sessions.json`、`sessions/<session-id>/state.json`和`events.jsonl`。`sessions.json`保存活动会话和元数据。旧根`state.json`首次被导入为一个会话后保持不变；不得用旧文件覆盖新目录。

## 读取端点

- `GET /`：返回聊天页面。
- `GET /api/health`：返回服务标识、应用版本、协议版本、实例、PID、epoch 和 revision。
- `GET /api/state?since=N`：返回从消息下标 N 开始的增量消息，以及完整 scene、session、participants 和 typing。
- `GET /api/state?since=N&session=<id>`：读取指定活动或归档会话，不改变CLI写入目标。
- `GET /api/sessions?include_archived=1`：列出会话目录。
- `GET /api/events?session=<id>&after=N`：按会话序号读取事件。
- `GET /api/stream?session=<id>&after_revision=N`：GET-only SSE 修订通知；客户端收到通知后重新读取 state。连接约20秒后由客户端重连，失败时回退轮询。

## 写入端点

- `POST /api/msg`：`{sender,text,sys?,ts?}`。
- `POST /api/typing`：`{sender,active}`；使用 `{clear:true}` 清除全部输入状态。
- `POST /api/participants`：`{participants:["成员甲","成员乙"]}`；替换完整名册，空数组表示清空。
- `POST /api/session`：`{session:{...}|null}`；原子替换会话计划，null恢复idle。
- `POST /api/scene`：`{scene:{title,subtitle}}`。
- `POST /api/reset`：`{scene:null|{title,subtitle}}`。
- `POST /api/seed`：`{scene?,session?,participants?,messages:[{sender,text,sys?,ts?}]}`。
- `POST /api/sessions`：`{title?,subtitle?,source?}`；创建并选择新会话。
- `POST /api/sessions/select|archive|restore`：`{session_id,source?}`。
- `POST /api/events`：提交一个标准事件，可用`session_id`指定目标，缺省为活动会话。
- `POST /api/events/batch`：`{session_id?,events:[...]}`；原子验证并提交1–5000个事件。
- `POST /api/decisions`：请求结构化人工决策，可包含完整 `session` 草案。
- `POST /api/decisions/resolve`：以 `approve|edit|reject|respond` 解决当前决策。
- `POST /api/shutdown`：停止匹配的本地服务。

所有 POST 使用 UTF-8 JSON 和 `Content-Type: application/json`。错误格式为：

```json
{"error":{"code":"invalid_sender","message":"sender must contain 1-64 characters"}}
```

## CLI

始终使用当前 `SKILL.md` 指定的命令入口。官方宿主中立包默认为 `scripts/live_chat.py`；宿主专用安装可能要求使用隔离 wrapper。以下示例以 `<entrypoint>` 表示该入口，全局参数必须放在子命令前：

```text
python <entrypoint> --state-dir <dir> --json status
python <entrypoint> --version
python <entrypoint> --url http://127.0.0.1:9000 msg 成员 --stdin
python <entrypoint> participants set "成员甲" "成员乙"
python <entrypoint> participants clear
python <entrypoint> session set --stdin
python <entrypoint> session set --file session.json
python <entrypoint> session clear
python <entrypoint> doctor --host codex
python <entrypoint> demo --lang zh-CN --port 0
python <entrypoint> sessions list --archived
python <entrypoint> sessions create --title "架构评审"
python <entrypoint> sessions select <session-id>
python <entrypoint> export <session-id> --format events --file history.json
python <entrypoint> replay --file history.json --speed 0
python <entrypoint> events emit --stdin
python <entrypoint> decision request --stdin
python <entrypoint> decision resolve <decision-id> approve --option-id approve
python <entrypoint> decision resolve <decision-id> respond --response "补充信息"
python <entrypoint> adapter show codex
```

消息正文来源三选一：位置参数、`--stdin`、`--file`。长文本或多行文本优先使用 stdin。

## 事件信封

客户端提交`type`、`source`和`payload`；可提供用于幂等重试的`event_id`和原始`occurred_at`。服务分配会话内单调`seq`并返回完整信封：

```json
{
  "event_version": 2,
  "event_id": "0d22f68a8a3046e6aa0c7e66d86ac9f9",
  "session_id": "f403426c5aa7465cb849c36eb042e9d8",
  "seq": 12,
  "type": "message.created",
  "occurred_at": "2026-08-12T12:00:00+00:00",
  "source": {"host": "codex", "actor": "Architect", "run_id": "opaque-run-id"},
  "payload": {"sender": "Architect", "text": "先明确状态边界。"}
}
```

事件类型为`conversation.created|selected|archived|restored|reset|seeded`、`scene.updated`、`plan.updated`、`participants.replaced`、`message.created`、`typing.changed|cleared`，以及 v2 的 `decision.requested|resolved`、`round.started|completed`、`participant.started|completed|failed`、`run.completed`。生命周期事件的 payload 包含原子替换后的完整 session，避免半更新状态。`source.host`为`codex|agents|claude|copilot|generic|manual|legacy`。相同`event_id`和相同内容为幂等成功；内容不同返回`event_conflict`。

决策请求的 `id` 是32位小写十六进制，`kind` 为 `plan_approval|clarification|model_fallback|checkpoint`，并带1–10个稳定选项。resolve 的 action 为 `approve|edit|reject|respond`；`edit` 和 `respond` 必须携带非空回复。解决后再次提交相同内容是幂等成功，不同内容返回冲突。浏览器只展示决策与结果，不提供写入控件。

旧POST端点在服务内部转换为事件。归档会话只读，活动会话不能归档，不提供永久删除。

## 导出格式

导出顶层为`{"format":"live-chat-export/v2","kind":"snapshot|events",...}`。snapshot适合紧凑交接；events保留顺序和来源。replay 同时接受 v1 与 v2，总是创建新会话、重新分配会话ID与seq，并在`source.replay_of`记录原事件ID。

seed JSON 示例：

```json
{
  "scene": {"title": "架构评审", "subtitle": "历史回放"},
  "participants": ["架构师", "审查员"],
  "session": {
    "status": "running",
    "background": "已有两个候选方案",
    "objective": "选择可实施的架构方案",
    "deliverable": "推荐方案、风险和下一步",
    "criteria": ["比较关键权衡", "明确残留风险"],
    "model_policy": {
      "default": "inherit",
      "reasoning_effort": "medium",
      "fallback": "ask"
    },
    "roles": [
      {
        "name": "架构师",
        "role": "领域专家",
        "focus": "可扩展性",
        "tone": "理性、简洁",
        "style": "先结论，再给权衡和建议",
        "instructions": ["明确标注假设", "不要回避残留风险"],
        "model": {
          "requested": "default",
          "effective": "host-managed",
          "reasoning_effort": "default",
          "fallback_reason": ""
        }
      },
      {
        "name": "审查员",
        "role": "质疑者",
        "focus": "失败模式",
        "tone": "直接但不讽刺",
        "style": "按风险严重度排序",
        "instructions": ["质疑未经验证的假设"],
        "model": {
          "requested": "quality-model",
          "effective": "balanced-model",
          "reasoning_effort": "high",
          "fallback_reason": "请求模型不可用，用户同意使用可用替代"
        }
      }
    ],
    "round": {
      "current": 1,
      "max": 3,
      "phase": "independent",
      "completed_participants": []
    },
    "workflow": {
      "strategy": "parallel_panel",
      "approval": "approved",
      "limits": {
        "max_rounds": 3,
        "max_participants": 3,
        "max_retries": 1,
        "wall_time_seconds": 900
      }
    },
    "pending_decision": null,
    "run": {
      "id": "run-20260815-1",
      "started_at": "",
      "updated_at": "",
      "participants": [],
      "round_summaries": []
    },
    "result": null,
    "stop_reason": ""
  },
  "messages": [
    {"sys": true, "text": "第 1 轮"},
    {"sender": "架构师", "text": "先明确状态边界。"}
  ]
}
```

## 限制

- JSON 请求体最大 5 MB。
- sender 1–64 字符，消息 1–100000 字符。
- title 1–200 字符，subtitle 0–500 字符。
- participants 最多100个去重姓名，每个姓名1–64字符。
- session状态为 `idle|running|paused|waiting_user|completed|stopped|partial_failure`。
- session阶段为 `not_started|independent|challenge|synthesis`；活动会话当前轮次为1–99且不超过上限。
- 活动session要求目标、交付物、1–5条完成条件和至少2个唯一角色；角色与已完成成员必须引用名册。
- workflow策略为`parallel_panel|sequential_pipeline|critic_revise|debate_judge`；新会话批准状态为`required|approved|bypassed|rejected`。迁移的旧会话使用只读兼容标记 `legacy`。显式 v2 workflow 在 `approved|bypassed` 前不得进入 running。
- workflow限制轮次1–99、参与者2–100、单角色重试0–3、可选墙钟时间1–86400秒。run的尝试次数和轮次摘要不得越过这些预算。
- completed 的显式 v2 workflow 必须包含 result，且每条 session criteria 都有唯一的 `met` 结果；达到轮次上限但未满足时使用 waiting_user 或 partial_failure。
- `model_policy.default`为1–200字符的宿主模型标识或`inherit`；`fallback`为`ask|inherit|available`，默认`ask`。
- 会话推理强度为`inherit|none|low|medium|high|xhigh|max`；角色推理强度还可使用`default`。
- 每个角色的tone和style最多500字符，instructions最多10条、每条最多500字符。模型requested为1–200字符，effective可为空或记录宿主确认值，fallback_reason最多500字符。
- seed 最多 5000 条消息。

reset保留participants但将session恢复为idle。seed未提供session时也使用idle；提供session但未提供participants时会从消息与角色补全名册。

旧版 `state.json` 没有participants时，从消息和typing按首次出现顺序推导；没有session时补为idle。旧角色缺少行为或模型字段时，补为空行为设置、`requested=default`和空effective；旧session缺少model_policy时补为`inherit/ask`默认策略。Schema 1 session 会补充 `workflow.approval=legacy`、空 run、空 pending_decision 与空 result，再以 Schema 2 原子保存；首次导入的旧根文件本身不改写。事件日志可混读 v1/v2，新事件始终写 v2。

首次使用中性默认目录且该目录为空时，只复制旧Codex状态目录中的`state.json`。不复制`instance.json`或日志，也不删除旧目录。显式设置`LIVE_CHAT_STATE_DIR`或`--state-dir`时不执行默认目录迁移。
