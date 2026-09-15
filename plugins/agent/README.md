# agent — Claude Code 活动监控

Claude Code 小人常驻，**状态靠形态而不是颜色**区分；右侧数字 = 活跃会话数，底部黄点 = 并行子 agent 数。

| 状态 | 小人 | 徽标 |
| --- | --- | --- |
| idle（没在跑） | 闭眼（眼缝变一横） | 灰 `Z` 上下慢飘 |
| busy（正在生成 / 跑工具） | 睁眼 + 四条腿交替长短（走路） | 无 |
| wait（等你批准权限 / 通知） | 睁眼 | 橙 `?` 闪烁 |

小人 16×10，按官方像素图量的格：身体 `x2..13 y0..7`、竖眼缝 `x4/x11 y2..3`、
手臂横贯 `y4..5`、四条腿 `x3,5,10,12 y8..9`；用 8 条 `df` 画成，眼睛是挖成底色的洞。
坐标常量见 `plugin.py` 的 `BODY / ARMS / EYES / LEGS`。

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

## 开机自启（macOS）

面板不记忆插件开关，重启后 agent 是关的。用 `--start` 让它自己开：

```bash
python3 pixbar_panel.py --start agent
```

配成登录项就用 launchd，放一份 `~/Library/LaunchAgents/com.pixdeck.panel.plist`：
`ProgramArguments` 填 `/usr/bin/python3 <PIXDECK>/pixbar_panel.py --start agent`，
加 `RunAtLoad` + `KeepAlive`（崩了自动拉起），日志指到 `~/Library/Logs/pixdeck.log`。

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.pixdeck.panel.plist   # 装
launchctl kickstart -k gui/$(id -u)/com.pixdeck.panel                             # 改完代码重启
launchctl bootout gui/$(id -u)/com.pixdeck.panel                                  # 卸
```

hook 本身不需要常驻进程：它写在 `~/.claude/settings.json` 里，每个会话自己触发。
