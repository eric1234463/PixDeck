#!/usr/bin/env python3
"""agent — Agent 监控（主信息流插件, macOS）

显示本机 Claude Code 的活动: 几个会话在跑、此刻是否正在生成/等你回应、并行子 agent 数。
数据优先来自 hook(准确、即时): 见 hook.py 与 README.md; 未装 hook 时退回轮询探测:
- 活跃会话 = ~/.claude/projects/*/*.jsonl 中近 ACTIVE 秒内有更新的(每份=一个会话)。
- 正在执行(busy) = 任一 claude 进程 CPU 超阈值(工具等待期间 transcript 不动, 故用 CPU 补)。
- 子 agent = 各会话 <session>/subagents/ 下近 SUB 秒内活动的 transcript 数。
画面: 左侧 Claude Code 小人常驻; 忙=扛锄头走路, 等你回应=闪问号, 闲=躺平睡觉飘 Z; 右侧两条用量条(5h/7d), 底部黄点=子 agent 数。

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
LIMITS = os.path.expanduser("~/.pixdeck/limits.json")   # statusline 写的 5h/7d 用量; 见 README
ACTIVE, SUB = 180, 40               # 活跃会话窗 / 子agent 窗(秒)
CPU_BUSY = 8.0                      # claude 进程 CPU 超此值视为"正在生成"
TICK = 0.4                          # 推帧/动画节奏
POLL_EVERY = 5                      # 每 5 帧(2s)重新扫描一次状态
WHITE, GRAY, SUBC, DIM = "#E9EBEE", "#5B626D", "#FFD000", "#2A3038"
AMBER = "#FF6400"                   # 等你回应(权限请求/通知)
CLAUDE = "#D97757"                  # Claude 品牌橙: 小人常驻此色, 不用颜色区分状态

# Claude Code 小人 16x10(按官方像素图量的格): 身体 + 两条竖眼缝 + 横贯手臂 + 四条腿。
#   ..############..
#   ..##.######.##..   <- 眼(挖空成背景色)
#   ################   <- 手臂横贯全宽
#   ..############..
#   ...#.#....#.#...   <- 四条腿
# 状态不靠颜色, 靠形态: 闲=躺平闭眼+飘 Z; 忙=扛锄头走路(腿交替+锄头挥动); 等待=睁眼+闪问号。
SX, SY = 0, 3                       # 小人在 52x16 上的左上角
BODY, ARMS = (2, 0, 12, 8), (0, 4, 16, 2)
EYES, LEGS = (4, 11), (3, 5, 10, 12)
SLEEP_BODY, SLEEP_ARMS = (2, 5, 12, 5), (0, 7, 16, 2)   # 睡: 身体压扁并落到底部
BG = "#000000"                      # 挖眼用: 设备底色
# 锄头两帧: (手柄起点x,y, 终点x,y, 锄刃x,y) — 扛起 / 落地, 与走路同步 = 一边走一边锄
HOE = [((16, 4), (19, 1), (19, 0)), ((16, 5), (19, 8), (19, 8))]
BADGEX, DOTX = 16, 19               # "Z"/"?" 占 x16..21(一个字 6px), 正好在小人与用量条之间
BARX, BARW = 23, 27                 # 用量条: 上=5 小时窗, 下=7 天窗; 留 1px 与徽标分开
BAR5H_Y, BAR7D_Y = 5, 10


def little(color, walk=None, sleep=False):
    """小人的 draw 指令。walk=0/1: 两组腿交替长短 + 扛锄头挥动; sleep: 躺平闭眼。"""
    body, arms = (SLEEP_BODY, SLEEP_ARMS) if sleep else (BODY, ARMS)
    legy, legh = (body[1] + body[3], 1) if sleep else (8, 2)
    d = [{"df": [body[0] + SX, body[1] + SY, body[2], body[3], color]},
         {"df": [arms[0] + SX, arms[1] + SY, arms[2], arms[3], color]}]
    for i, x in enumerate(LEGS):
        h = legh if walk is None or i % 2 == walk else 1
        d.append({"df": [x + SX, legy + SY, 1, h, color]})
    if walk is not None:                                 # 手上的锄头
        (hx, hy), (tx, ty), (bx, by) = HOE[walk]
        d.append({"dl": [hx + SX, hy + SY, tx + SX, ty + SY, color]})      # 手柄
        d.append({"df": [bx + SX, by + SY, 2, 2, color]})                  # 锄刃
    for x in EYES:
        ey = body[1] + 1 if sleep else 2        # 睡: 眼睛落在身体上半, 别打到手臂那条横杠
        d.append({"df": [x - 1 + SX, ey + SY, 3, 1, BG]} if sleep         # 闭眼: 一横(3 宽, 与睁眼同心)
                 else {"df": [x + SX, 2 + SY, 1, 2, BG]})                 # 睁眼: 竖缝
    return d


def _pct(d, now):
    """一个窗口的已用百分比; 窗口已经滚过(now >= resets_at)则归零。"""
    if not d:
        return None
    return 0 if now >= (d.get("resets_at") or 0) else d.get("used_percentage")


def read_limits():
    """读 statusline 写的用量 -> (5h%, 7d%); 没装/读不到返回 None。"""
    try:
        with open(LIMITS) as f:
            d = json.load(f)
    except Exception:
        return None
    now = time.time()
    a, b = _pct(d.get("five_hour"), now), _pct(d.get("seven_day"), now)
    return None if a is None or b is None else (a, b)


def bar(y, pct, phase):
    """一条用量条: 暗底槽 + 亮填充; 用满 90% 以上闪烁提醒。"""
    d = [{"df": [BARX, y, BARW, 2, DIM]}]
    n = round(pct / 100 * BARW)
    if n and (pct < 90 or (phase // 2) % 2 == 0):
        d.append({"df": [BARX, y, max(1, n), 2, WHITE]})
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
    """小人常驻(忙=扛锄头走路, 等待=问号, 闲=躺平); 右侧两条用量条: 上 5 小时窗, 下 7 天窗。"""
    draw = little(CLAUDE, walk=phase % 2 if busy else None, sleep=not busy and not wait)
    lim = read_limits()
    if lim:
        draw += bar(BAR5H_Y, lim[0], phase) + bar(BAR7D_Y, lim[1], phase)
    text = []
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
