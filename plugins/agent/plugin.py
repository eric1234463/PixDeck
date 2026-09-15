#!/usr/bin/env python3
"""agent — Agent 监控（主信息流插件, macOS）

显示本机 Claude Code 的活动: 几个会话在跑、此刻是否正在生成/等你回应、并行子 agent 数。
数据优先来自 hook(准确、即时): 见 hook.py 与 README.md; 未装 hook 时退回轮询探测:
- 活跃会话 = ~/.claude/projects/*/*.jsonl 中近 ACTIVE 秒内有更新的(每份=一个会话)。
- 正在执行(busy) = 任一 claude 进程 CPU 超阈值(工具等待期间 transcript 不动, 故用 CPU 补)。
- 子 agent = 各会话 <session>/subagents/ 下近 SUB 秒内活动的 transcript 数。
画面: 左侧大数字=会话数(忙绿/等橙/闲白); 右侧扫描灯(忙时脉动、等待时闪烁); 底部黄点=子 agent 数。

单独运行: python3 plugins/agent/plugin.py [--device IP] [--dry-run] [--once]
"""
import os, sys, glob, json, re, subprocess, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pixbar_core as core

APP = "agent"
NAME = "Agent 监控"
GROUP = "信息"
DESC = "显示本机 Claude Code 会话：几个在跑、是否正在执行、并行子 agent 数。"
DEFAULT_INTERVAL = 1
ITEMS = ["agent"]

PROJ = os.path.expanduser("~/.claude/projects")
HOOKDIR = os.path.expanduser("~/.pixdeck/agent")   # hook.py 写的会话状态; 目录不存在=未装 hook
ACTIVE, SUB = 180, 40               # 活跃会话窗 / 子agent 窗(秒)
CPU_BUSY = 8.0                      # claude 进程 CPU 超此值视为"正在生成"
TICK = 0.4                          # 推帧/动画节奏
POLL_EVERY = 5                      # 每 5 帧(2s)重新扫描一次状态
SCANX0, SCANX1 = 22, 49            # 扫描灯轨道
GREEN, WHITE, DIM, GRAY, SUBC = "#3EE08A", "#E9EBEE", "#2A3038", "#5B626D", "#FFD000"
AMBER = "#FF6400"                   # 等你回应(权限请求/通知)


def _busy():
    """任一 claude 进程 CPU 超阈值 = 此刻在生成。"""
    try:
        out = subprocess.run(["ps", "-axo", "pcpu,args"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    for ln in out.splitlines():
        if re.search(r"\bclaude\b", ln) and "plugins/" not in ln and "grep" not in ln:
            try:
                if float(ln.split()[0]) > CPU_BUSY:
                    return True
            except Exception:
                pass
    return False


def scan_hooks():
    """读 hook 状态目录 -> (会话数, 子agent数, busy, wait); 未装 hook 返回 None。"""
    if not os.path.isdir(HOOKDIR):
        return None
    now = time.time()
    sessions = subs = 0
    busy = wait = False
    for f in glob.glob(HOOKDIR + "/*.json"):
        try:
            with open(f) as fh:
                st = json.load(fh)
        except Exception:
            continue
        if now - st.get("ts", 0) > ACTIVE:      # 会话崩了没发 SessionEnd: 超时即视为结束
            continue
        sessions += 1
        subs += st.get("subs", 0)
        busy = busy or st.get("state") == "busy"
        wait = wait or st.get("state") == "wait"
    return sessions, subs, busy, wait


def scan():
    """返回 (活跃会话数, 子agent数, busy, wait)。有 hook 用 hook, 否则轮询探测。"""
    hooked = scan_hooks()
    if hooked is not None:
        return hooked
    now = time.time()
    sessions = subs = 0
    for f in glob.glob(PROJ + "/*/*.jsonl"):
        if now - os.path.getmtime(f) > ACTIVE:
            continue
        sessions += 1
        sid = os.path.splitext(os.path.basename(f))[0]
        sub_dir = os.path.join(os.path.dirname(f), sid, "subagents")
        if os.path.isdir(sub_dir):
            subs += sum(1 for s in glob.glob(sub_dir + "/*.jsonl") if now - os.path.getmtime(s) < SUB)
    return sessions, subs, _busy(), False


def render(sessions, subs, busy, wait, phase, interval):
    if sessions == 0:
        return {"duration": interval, "text": [
            {"content": "IDLE", "fontHeight": 10, "x": -1000, "y": 3,
             "align": "center", "rect": [0, 0, 52, 16], "color": GRAY}]}
    color = AMBER if wait else (GREEN if busy else WHITE)
    text = [{"content": str(sessions), "fontHeight": 10, "x": 3, "y": 3, "color": color}]
    draw = []
    span = SCANX1 - SCANX0
    if wait:                                       # 等你回应: 整条轨道闪烁
        draw.append({"df": [SCANX0, 7, span + 2, 2, AMBER if (phase // 2) % 2 == 0 else DIM]})
    elif busy:                                       # 扫描灯来回扫
        p = phase % (2 * span)
        x = SCANX0 + (p if p <= span else 2 * span - p)
        draw.append({"df": [x, 7, 3, 2, GREEN]})
        draw.append({"dl": [SCANX0, 8, SCANX1 + 2, 8, DIM]})
    else:                                          # 闲: 轨道上一颗暗点
        draw.append({"dl": [SCANX0, 8, SCANX1 + 2, 8, DIM]})
        draw.append({"df": [(SCANX0 + SCANX1) // 2, 7, 2, 2, GRAY]})
    for k in range(min(subs, 10)):                 # 底部子agent 黄点
        draw.append({"df": [SCANX0 + k * 3, 13, 2, 2, SUBC]})
    return {"duration": interval, "text": text, "draw": draw}


def frame_for(item, interval):
    s, sub, b, w = scan()
    return render(s, sub, b, w, 0, interval), f"sessions={s} busy={b} wait={w} subs={sub}"


def run_loop(device, interval, stop=None, log=print, dry_run=False, once=False, options=None):
    i = 0
    cached = (0, 0, False, False)
    while stop is None or not stop.is_set():
        if i % POLL_EVERY == 0:
            cached = scan()
        s, sub, b, w = cached
        frame = render(s, sub, b, w, i, interval)
        if not dry_run:
            try: core.push(device, APP, frame)
            except Exception as e: log(f"  push fail: {e}")
        elif i % POLL_EVERY == 0:
            log(f"{time.strftime('%H:%M:%S')}  sessions={s} busy={b} wait={w} subs={sub}")
        i += 1
        if once:
            break
        if stop is not None:
            if stop.wait(TICK):
                break
        else:
            time.sleep(TICK)


if __name__ == "__main__":
    core.standalone(sys.modules[__name__])
