# agent — Claude Code 活动监控

Claude Code 小人常驻，**状态靠形态而不是颜色**区分；右侧数字 = 活跃会话数，底部黄点 = 并行子 agent 数。

| 状态 | 小人 | 徽标 |
| --- | --- | --- |
| idle（没在跑） | 躺平：身体压扁落到底部、腿收起、闭眼 | 灰 `Z` 上下慢飘 |
| busy（正在生成 / 跑工具） | 扛锄头走路：四条腿交替长短 + 锄头两帧挥动 | 无 |
| wait（等你批准权限 / 通知） | 睁眼 | 橙 `?` 闪烁 |

小人 16×10，按官方像素图量的格：身体 `x2..13 y0..7`、竖眼缝 `x4/x11 y2..3`、
手臂横贯 `y4..5`、四条腿 `x3,5,10,12 y8..9`；用 `df` 画成，眼睛是挖成底色的洞。
锄头是一条 `dl` 手柄加一个 2×2 锄刃，两帧坐标在 `HOE`；躺平用 `SLEEP_BODY / SLEEP_ARMS`。
坐标常量都在 `plugin.py` 顶部。

右侧两条用量条（`x22..49`，各 2px 高）：**上 = 5 小时窗，下 = 7 天窗**。
暗槽是总量，亮段是已用；任一条到 90% 以上会闪烁。底部黄点 = 并行 subagent 数，最多画 4 个。

### 用量数据从哪来

只有 statusline 的 stdin JSON 带 `rate_limits`（`policy-limits.json`、`stats-cache.json`、
transcript 都没有）。所以在 `~/.claude/statusline-command.sh` 开头加一段，把它原子写到
`~/.pixdeck/limits.json`：

```bash
mkdir -p ~/.pixdeck 2>/dev/null && printf '%s' "$input" \
  | jq -c '{five_hour: .rate_limits.five_hour, seven_day: .rate_limits.seven_day, ts: now}' \
  > ~/.pixdeck/limits.json.tmp 2>/dev/null && mv ~/.pixdeck/limits.json.tmp ~/.pixdeck/limits.json
```

statusline 只在有 Claude Code 会话在渲染时才跑，所以没开 Claude Code 时这个文件不更新；
插件按 `resets_at` 判断窗口是否已经滚过，滚过就当 0%，因此陈旧文件也不会显示错的高用量。
读不到文件就不画这两条，小人照常显示。

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
