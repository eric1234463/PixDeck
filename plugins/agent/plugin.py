#!/usr/bin/env python3
"""agent — Agent 监控（主信息流插件, macOS）

显示本机 Claude Code 的活动: 几个会话在跑、此刻是否正在生成/等你回应、并行子 agent 数。
数据优先来自 hook(准确、即时): 见 hook.py 与 README.md; 未装 hook 时退回轮询探测:
- 活跃会话 = ~/.claude/projects/*/*.jsonl 中近 ACTIVE 秒内有更新的(每份=一个会话)。
- 正在执行(busy) = 任一 claude 进程 CPU 超阈值(工具等待期间 transcript 不动, 故用 CPU 补)。
- 子 agent = 各会话 <session>/subagents/ 下近 SUB 秒内活动的 transcript 数。
画面: 左侧 Claude Code 小人常驻; 忙=走路, 等你回应=闪问号, 闲=闭眼飘 Z; 右侧会话数, 底部黄点=子 agent 数。

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
WHITE, GRAY, SUBC = "#E9EBEE", "#5B626D", "#FFD000"
AMBER = "#FF6400"                   # 等你回应(权限请求/通知)
CLAUDE = "#D97757"                  # Claude 品牌橙: 小人常驻此色, 不用颜色区分状态

# Claude Code 小人 16x10(按官方像素图量的格): 身体 + 两条竖眼缝 + 横贯手臂 + 四条腿。
#   ..############..
#   ..##.######.##..   <- 眼(挖空成背景色)
#   ################   <- 手臂横贯全宽
#   ..############..
#   ...#.#....#.#...   <- 四条腿
# 状态不靠颜色, 靠形态: 闲=闭眼+飘 Z; 忙=四条腿交替长短(走路); 等待=睁眼+闪问号。
SX, SY = 0, 3                       # 小人在 52x16 上的左上角
BODY, ARMS = (2, 0, 12, 8), (0, 4, 16, 2)
EYES, LEGS = (4, 11), (3, 5, 10, 12)
BG = "#000000"                      # 挖眼用: 设备底色
BADGEX, COUNTX, DOTX = 19, 33, 19   # "Z"/"?" / 会话数 / 子agent 黄点 的 x


def little(color, walk=None, sleep=False):
    """小人的 draw 指令。walk=0/1 时两组腿交替长短(走路动画); sleep 时眼睛闭成一横。"""
    d = [{"df": [BODY[0] + SX, BODY[1] + SY, BODY[2], BODY[3], color]},
         {"df": [ARMS[0] + SX, ARMS[1] + SY, ARMS[2], ARMS[3], color]}]
    for i, x in enumerate(LEGS):
        h = 2 if walk is None or i % 2 == walk else 1
        d.append({"df": [x + SX, 8 + SY, 1, h, color]})
    for x in EYES:
        d.append({"df": [x - 1 + SX, 3 + SY, 3, 1, BG]} if sleep     # 闭眼: 一横(3 宽, 与睁眼同心)
                 else {"df": [x + SX, 2 + SY, 1, 2, BG]})            # 睁眼: 竖缝
    return d


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
    """小人常驻; 三态靠形态: 忙=走路, 等待=问号闪烁, 闲=闭眼飘 Z。"""
    draw = little(CLAUDE, walk=phase % 2 if busy else None, sleep=not busy and not wait)
    text = []
    if sessions:
        text.append({"content": str(sessions), "fontHeight": 10, "x": COUNTX, "y": 3, "color": WHITE})
    if wait:                                       # 等你回应: 问号闪烁
        if (phase // 2) % 2 == 0:
            text.append({"content": "?", "fontHeight": 10, "x": BADGEX, "y": 3, "color": AMBER})
    elif not busy:                                 # 闲: Z 慢慢上下飘
        text.append({"content": "Z", "fontHeight": 10, "x": BADGEX,
                     "y": 2 if (phase // 4) % 2 else 4, "color": GRAY})
    for k in range(min(subs, 4)):                  # 底部子agent 黄点
        draw.append({"df": [DOTX + k * 3, 14, 2, 2, SUBC]})
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
