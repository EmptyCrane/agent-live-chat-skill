# Agent Live Chat Skill

[English README](README.md)

这是一个遵循开放 [Agent Skills](https://agentskills.io/) 格式的多智能体群聊直播项目。它通过零运行时第三方依赖的Python本地服务，把真实子智能体回复按完成顺序展示在只读浏览器页面中，并提供目标驱动轮次、typing、参与者状态、主题、持久化和回放。

> **Beta状态：** Codex已验证；Claude Code和GitHub Copilot采用相同开放Skill格式，但在完成对应宿主冒烟测试前标记为实验性。

![Agent Live Chat中文界面](docs/images/live-chat-zh-CN.png)

## 主要能力

- 只展示真实子智能体回复，主智能体不伪造台词。
- 默认选择2–3个互补角色，采用“目标优先、最多3轮”的软护栏。
- 分离保存角色职责、语气、表达方式、行为规则、请求模型、实际模型和推理强度。
- 支持暂停、继续、停止、等待用户、部分失败和提前完成状态。
- 服务只监听`127.0.0.1`，浏览器页面保持只读。
- 运行时仅依赖Python标准库，不需要API Key。
- 状态与Skill代码分离，可恢复、迁移和回放历史对话。
- 自适应单栏/双栏布局，提供自动、浅色和深色主题。

## 工作方式

宿主智能体负责需求采集、派发子智能体和判断目标；Python服务只负责保存与展示，不调用任何LLM。回复按真实完成顺序通过CLI或localhost API推送到页面。

如果宿主没有子智能体能力，Skill会保留服务、回放和手动推送模式，并明确说明实时多智能体编排不可用；不会用单一智能体模拟多个角色。

## 兼容性

| 宿主 | Skill目录 | 多智能体直播 | v0.1状态 |
| --- | --- | --- | --- |
| OpenAI Codex | `.agents/skills` | 宿主提供子智能体时可用 | 已验证 |
| Claude Code | `.claude/skills` | 宿主提供子智能体时可用 | 实验性 |
| GitHub Copilot | `.github/skills`、`.copilot/skills`或`.agents/skills` | 取决于宿主表面 | 实验性 |

## 安装

需要Python 3.9或更高版本。克隆仓库后先预览：

```bash
python tools/install.py --host codex --scope user
```

确认目标后应用：

```bash
python tools/install.py --host codex --scope user --apply
```

Claude Code或GitHub Copilot分别使用`--host claude`或`--host copilot`。`--host auto`只有在恰好识别到一个现有宿主Skill根目录时才会继续；没有匹配或存在多个匹配时会终止并要求显式指定。

安装到所有支持宿主：

```bash
python tools/install.py --host all --scope user --apply
```

项目级安装：

```bash
python tools/install.py --host codex --scope project --apply
```

安装器默认只做dry-run。目标存在时会终止；只有显式提供`--replace`才会先把旧目录重命名为带时间戳的备份，然后安装新版。

也可以手动把`skill/live-chat`复制到对应的Skill目录，并保持目录名为`live-chat`。

## 使用

示例请求：

> 对这个方案进行三角色直播评审：Architect语气理性简洁，Critic直接但尊重他人，Operator务实；Critic请求当前宿主最强的可用模型，其他角色继承宿主模型；达到验收条件时提前结束。

支持显式Skill调用的宿主也可使用`$live-chat`。如果目标和交付物已经明确，Skill不会重复询问。

模型标识属于宿主能力，不是角色人设。精确请求的模型不可用时默认暂停并询问，不会静默映射为其他厂商模型；宿主能够确认时，页面会分别展示请求模型和实际模型。

CLI示例：

```bash
python skill/live-chat/scripts/live_chat.py --version
python skill/live-chat/scripts/live_chat.py --json start
python skill/live-chat/scripts/live_chat.py status
python skill/live-chat/scripts/live_chat.py participants set Architect Critic Operator
python skill/live-chat/scripts/live_chat.py msg Architect "Initial proposal"
python skill/live-chat/scripts/live_chat.py stop
```

默认运行数据路径：

- Windows：`%LOCALAPPDATA%\agent-live-chat`
- macOS：`~/Library/Application Support/agent-live-chat`
- Linux：`$XDG_STATE_HOME/agent-live-chat`，未设置时为`~/.local/state/agent-live-chat`

可通过`LIVE_CHAT_STATE_DIR`覆盖。新版首次启动且中性目录为空时，只复制旧Codex目录中的`state.json`；不会迁移PID、实例文件或日志，也不会删除旧数据。

## 安全边界

- 服务只绑定回环地址，不应通过代理暴露到公网。
- 页面不发送写请求，所有状态修改来自本地CLI/API。
- 聊天内容仅保存在本机`state.json`；日志默认不记录完整正文。
- 安装器拒绝符号链接目标，不提供递归卸载。
- 第三方Skill包含可执行说明与脚本，安装前应审查源码。

更多信息见[SECURITY.md](SECURITY.md)。

## 开发与发布

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python tools/package_release.py --version 0.1.0-beta.4
```

视觉测试需要Node.js和锁定的Playwright开发依赖：

```bash
npm ci
npx playwright install chromium
```

发布ZIP只包含`skill/live-chat`运行文件和SHA-256校验文件，不包含测试、文档、缓存、状态或日志。

## 已知限制

- `v0.1.0-beta.4`保持HTTP协议和状态Schema版本1不变，新增真实中英文本地化、配套双语截图、隐私安全发布审计与无歧义宿主安装。

- Claude Code与Copilot目前完成格式兼容检查，但尚未完成对应宿主实测。
- 自动打开浏览器和强制中断运行中子智能体依赖宿主能力。
- 子智能体模型覆盖与推理强度取决于当前宿主工具；宿主不公开实际模型时显示为`host-managed`，不进行猜测。
- GitHub托管macOS runner目前受[runner-images #14409](https://github.com/actions/runner-images/issues/14409)影响，无法执行分离进程localhost生命周期测试；macOS仍运行其余单测、HTTP、安装器和打包检查，POSIX分离进程生命周期由Ubuntu验证。
- 本地服务面向单机可信用户，不适合公网、多租户或未受信网络。

## 许可证

MIT，见[LICENSE](LICENSE)。
