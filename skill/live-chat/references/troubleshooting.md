# 故障排查

宿主能力差异和安装目录见 `hosts.md`。没有子智能体时只能使用回放或手动推送模式，不得把单智能体模拟描述成真实多智能体直播。

以下命令中的 `<entrypoint>` 必须替换为当前 `SKILL.md` 指定的入口；宿主专用安装可能要求使用隔离 wrapper。

## 查看状态

运行：

```text
python <entrypoint> status
```

它会显示实际 URL、PID、消息数、参与者名册、typing成员、session状态、轮次、状态文件和日志位置。

先运行 `python <entrypoint> doctor`。退出码 `0` 表示全部通过，`2` 表示仅有警告，`1` 表示至少一项失败。`FAIL`必须解决；`WARN`表示服务尚未启动、尚未产生状态或发现可能的重复安装，不一定阻止使用。

## 找不到历史会话

运行 `sessions list --archived`。浏览器历史选择器只读，不会改变CLI写入目标；需要继续某个会话时使用 `sessions select <session-id>`。归档会话必须先restore才能选择。

## 连接到旧服务

旧命令可继续控制协议版本 1 服务。若多会话、事件、导出或回放命令返回 `unsupported_feature`，停止旧服务并从同一状态目录启动当前版本；旧根 `state.json` 会被非破坏地导入。

## 名册人数少于预期

在派发前运行 `participants set` 登记全体成员。页面只会自动补充已经发言或正在输入的新成员，无法猜测尚未出现的名字。

## 服务未启动

运行 start 并使用输出的实际 URL。8765 被其他程序占用时服务会自动选择空闲端口，不要假设 URL 永远是8765。

## 页面显示重连中

先运行 status。服务正常时刷新页面；服务不存在时重新 start。状态保存在独立快照中，重启后会恢复。

## 输入提示残留

运行：

```text
python <entrypoint> typing --clear
```

同时检查编排流程是否在失败、超时和取消路径关闭 typing。

## 暂停后无法准确恢复

运行status并检查session的当前轮次与 `completed_participants`。继续时只派发角色列表中尚未完成的成员；原实例不可用时重建相同角色并提供历史摘要。若session与名册不一致，先clear session，再更新名册和完整session，不要直接删除状态文件。

## 达到轮数上限仍未收敛

将session设为 `waiting_user` 并在stop_reason列出缺少的完成条件。等待用户选择追加轮次、调整角色、修改目标或接受部分结果；不要自动无限追加。

## 状态文件损坏

查看 `server.log` 中的加载错误。不要手工覆盖损坏文件或删除用户历史；先复制状态目录，再决定恢复来源。

## 旧历史迁移

新版首次启动且不存在 `state.json` 时，只读重放旧 `messages.jsonl` 并生成快照。旧记录没有session时使用idle。原文件不会被移动或修改。可用 `--no-legacy` 禁用自动导入。

## 无法自动打开浏览器

直接把 start 输出的 URL 交给用户。页面是否自动打开不影响服务和推送命令。Skill 只请求右侧页签和 URL，不设置内置浏览器尺寸；未触发 Skill 时不会调用浏览器能力。
