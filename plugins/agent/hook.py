#!/usr/bin/env python3
"""agent/hook.py — Claude Code hook -> 状态文件(供 agent 插件读)

Claude Code 每次 hook 事件调用本脚本, 事件名走 argv[1], session_id 从 stdin JSON 取。
每个会话一份 ~/.pixdeck/agent/<session>.json; 写时 flock, 免得并发事件互相覆盖。
Notification 不在 STATE 里: 它在回合结束时也会 fire(在 Stop 之后), 会把 idle 盖成 wait;
不映射它就只刷新 ts(让会话留在活跃窗口内), 状态保持不变。
插件读这个目录就知道: 几个会话在跑、谁在执行、谁在等你回应、几个子 agent。

注册方式见同目录 README.md。本脚本永远 exit 0 且不输出, 不影响 Claude Code。
"""
import fcntl, json, os, sys, time

DIR = os.path.expanduser("~/.pixdeck/agent")
STATE = {                                   # 事件 -> 会话状态
    "SessionStart": "idle",
    "UserPromptSubmit": "busy", "PreToolUse": "busy",
    "PostToolUse": "busy", "PostToolUseFailure": "busy",
    "PermissionRequest": "wait",        # 只有权限请求才算"等你回应"
    "Stop": "idle", "StopFailure": "idle",
}
SUB_DELTA = {"SubagentStart": 1, "SubagentStop": -1}
RESET_SUBS = ("SessionStart", "Stop", "StopFailure")   # 回合结束=没有子 agent 在跑, 清掉漏掉的 SubagentStop


def main():
    ev = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    sid = str(payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or os.getppid())
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, sid + ".json")
    if ev == "SessionEnd":
        try: os.remove(path)
        except OSError: pass
        return
    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try: st = json.loads(f.read() or "{}")
        except ValueError: st = {}
        st["state"] = STATE.get(ev, st.get("state", "idle"))       # 子 agent 事件不改状态
        st["subs"] = 0 if ev in RESET_SUBS else max(0, st.get("subs", 0) + SUB_DELTA.get(ev, 0))
        st["ts"] = time.time()
        f.seek(0); f.truncate(); f.write(json.dumps(st))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass                                 # hook 绝不因自身出错拖累 Claude Code
