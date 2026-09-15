#!/usr/bin/env python3
"""agent — Agent 监控（主信息流插件, macOS）

显示本机 Claude Code 的活动: 几个会话在跑、此刻是否正在生成/等你回应、并行子 agent 数。
数据优先来自 hook(准确、即时): 见 hook.py 与 README.md; 未装 hook 时退回轮询探测:
- 活跃会话 = ~/.claude/projects/*/*.jsonl 中近 ACTIVE 秒内有更新的(每份=一个会话)。
- 正在执行(busy) = 任一 claude 进程 CPU 超阈值(工具等待期间 transcript 不动, 故用 CPU 补)。
- 子 agent = 各会话 <session>/subagents/ 下近 SUB 秒内活动的 transcript 数。
画面: 左侧 Claude 星芒图标(闲灰/忙橙/等待黄), 右侧会话数 + 状态区, 底部黄点=子 agent 数。

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
WHITE, DIM, GRAY, SUBC = "#E9EBEE", "#2A3038", "#5B626D", "#FFD000"
AMBER = "#FF6400"                   # 等你回应(权限请求/通知)
CLAUDE = "#D97757"                  # Claude 品牌橙: 正在工作

# Claude 星芒图标 13x13: 8 条射线(正向长 6, 斜向长 3)。用 6 条 dl 画完, 比 db 位图省很多字节。
# 形状(x,y 均以图标左上角为原点):
#   ......#......      正向射线 = 一竖一横贯穿
#   ...#..#..#...      斜向射线 = 4 段短对角
#   .....###.....
#   #############
ICON = [(6, 0, 6, 12), (0, 6, 12, 6),                      # 竖 / 横
        (3, 3, 5, 5), (9, 3, 7, 5), (3, 9, 5, 7), (9, 9, 7, 7)]   # 左上/右上/左下/右下
IX, IY = 0, 1                       # 图标在 52x16 上的左上角
SCANX0, SCANX1 = 23, 49             # 状态区(图标与数字右边)


def icon(color):
    """Claude 星芒的 draw 指令列表。"""
    return [{"dl": [x0 + IX, y0 + IY, x1 + IX, y1 + IY, color]} for x0, y0, x1, y1 in ICON]


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
    """三态: 等你回应(黄+问号闪烁) > 正在工作(橙+扫描灯) > 闲(灰图标)。"""
    color = AMBER if wait else (CLAUDE if busy else (WHITE if sessions else GRAY))
    draw = icon(color)
    text = []
    if sessions:
        text.append({"content": str(sessions), "fontHeight": 10, "x": 15, "y": 3, "color": color})
    span = SCANX1 - SCANX0
    if wait:                                       # 等你回应: 问号闪烁
        if (phase // 2) % 2 == 0:
            text.append({"content": "?", "fontHeight": 10, "x": 28, "y": 3, "color": AMBER})
    elif busy:                                     # 正在工作: 扫描灯来回扫
        draw.append({"dl": [SCANX0, 8, SCANX1 + 2, 8, DIM]})
        p = phase % (2 * span)
        x = SCANX0 + (p if p <= span else 2 * span - p)
        draw.append({"df": [x, 7, 3, 2, CLAUDE]})
    for k in range(min(subs, 8)):                  # 底部子agent 黄点
        draw.append({"df": [SCANX0 + k * 3, 14, 2, 2, SUBC]})
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
