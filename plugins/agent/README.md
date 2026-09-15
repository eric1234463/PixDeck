# agent — Claude Code 活动监控

左侧 Claude 星芒图标 = 状态（靠颜色区分），右侧数字 = 活跃会话数，底部黄点 = 并行子 agent 数。

| 状态 | 图标颜色 | 右侧 |
| --- | --- | --- |
| 没有会话 | 灰 `#5B626D` | 空 |
| idle（有会话但没在跑） | 白 `#E9EBEE` | 会话数 |
| busy（正在生成 / 跑工具） | Claude 橙 `#D97757` | 会话数 + 扫描灯来回扫 |
| wait（等你批准权限 / 通知） | 橙黄 `#FF6400` | 会话数 + 闪烁 `?` |

图标是 13×13 的 8 射线星芒，用 6 条 `dl` 画成（比 `db` 位图省字节）；坐标见 `plugin.py` 的 `ICON`。

## 数据来源

优先读 hook 写的 `~/.pixdeck/agent/<session>.json`（准确、即时）。
该目录不存在时自动退回轮询探测（扫 transcript mtime + `ps` 上 claude 进程的 CPU），
无需配置即可用，但分不出 "在生成" 和 "等你回应"。

## 装 hook（推荐）

把下面的 `command` 加进 `~/.claude/settings.json` 的 `hooks` 里，每个事件一条。
已有其他 hook（Ulanzi / otty 等）的事件，追加到同一个 matcher 的 `hooks` 数组即可，不要替换。

```json
{
  "hooks": {
    "SessionStart":       [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 <PIXDECK>/plugins/agent/hook.py SessionStart" }] }],
    "UserPromptSubmit":   [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 <PIXDECK>/plugins/agent/hook.py UserPromptSubmit" }] }],
    "PreToolUse":         [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 <PIXDECK>/plugins/agent/hook.py PreToolUse" }] }],
    "PostToolUse":        [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 <PIXDECK>/plugins/agent/hook.py PostToolUse" }] }],
    "Notification":       [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 <PIXDECK>/plugins/agent/hook.py Notification" }] }],
    "PermissionRequest":  [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 <PIXDECK>/plugins/agent/hook.py PermissionRequest" }] }],
    "SubagentStart":      [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 <PIXDECK>/plugins/agent/hook.py SubagentStart" }] }],
    "SubagentStop":       [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 <PIXDECK>/plugins/agent/hook.py SubagentStop" }] }],
    "Stop":               [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 <PIXDECK>/plugins/agent/hook.py Stop" }] }],
    "SessionEnd":         [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python3 <PIXDECK>/plugins/agent/hook.py SessionEnd" }] }]
  }
}
```

`<PIXDECK>` 换成本仓库绝对路径。hook 本身不输出、永远 exit 0，出错也不影响 Claude Code。
会话崩溃没发 `SessionEnd` 时，状态文件超过 180 秒未更新即自动视为结束。

自测：

```bash
echo '{"session_id":"t1"}' | python3 plugins/agent/hook.py PreToolUse
python3 plugins/agent/plugin.py --dry-run --once     # 应打印 sessions=1 busy=True
```

## 推到时钟

画面帧走框架统一的传输层：面板设置里选 HTTP（默认，POST 到设备）或 MQTT
（发布到 `<prefix>/custom/agent`）。插件本身不关心用哪种。
