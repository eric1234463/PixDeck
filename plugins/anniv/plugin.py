#!/usr/bin/env python3
"""anniv — 纪念日 -> 像素时钟 DIY "anniv"（插件）

轮播三屏, 每屏一张 14px 卡比像素画 + 一个短数字, 全部静止不滚动:
  名字  AMY + 爱心 + BEN   (名字太长时退回首字母 K + S)
  在一起 卡比 + 天数       5152   (右下角一颗心)
  倒数  星星 + 剩余天数    326    (周年当天换成 HAPPY!)
左 16px 放图、右 36px 放字(设备字体 6px 一个, 即最多 6 个字), 所以只放数字不放句子 —
一眼能读完才是这块 52x16 屏的用法, 长句跑马灯要盯着看完, 反而不好用。

名字与日期在面板文本选项里填, 改了下一屏即时生效, 并写回 .pixbar.json:
这两个值长期不变, 不该每次重填; 也不该写死在仓库里(origin 是公开 repo)。

单独运行(家里那台常年挂着): python3 plugins/anniv/plugin.py --device IP --set date=2019-02-14
(--set 只需给一次, 之后 python3 plugins/anniv/plugin.py --device IP 就够。)
"""
import datetime, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pixbar_core as core

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".pixbar.json")
CONFIG_KEYS = {"date": "anniv_date", "names": "anniv_names"}


def _saved(key):
    """从 .pixbar.json 读上次用的值; 读不到返回空串(退回 OPTIONS 里的占位默认)。"""
    try:
        with open(CONFIG_PATH) as f:
            d = json.load(f)
        return str(d.get(CONFIG_KEYS[key], "")) if isinstance(d, dict) else ""
    except Exception:
        return ""


def _remember(key, value):
    """写回 .pixbar.json。必须读-改-写: 同一份文件还存着设备地址与传输设置, 整份覆盖会丢掉它们;
    因此文件存在但解析失败时宁可不写(不同于面板 _load_config 的静默 {}, 那条路会丢键)。"""
    try:
        d = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                d = json.load(f)
            if not isinstance(d, dict):
                return
        d[CONFIG_KEYS[key]] = value
        with open(CONFIG_PATH, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


APP = "anniv"
NAME = "纪念日"
GROUP = "工具"
DESC = "纪念日：卡比和 Yukio 一边一个，中间是你们在一起的天数。"
DEFAULT_INTERVAL = 6
ITEMS = ["names", "days"]
OPTIONS = [
    {"key": "names", "label": "名字(用 & 隔开)", "type": "text", "default": _saved("names") or "US & TWO"},
    {"key": "date", "label": "纪念日(YYYY-MM-DD)", "type": "text", "default": _saved("date") or "2020-01-01"},
    core.color_option("#FFD000"),     # 数字默认琥珀色: 粉色数字跟粉色卡比在两米外糊成一片
]
SCREEN_W, SCREEN_H = 52, 16
TEXT_X, TEXT_W, CHAR_W = 16, 36, 6    # 左 16px 留给图, 右 36px 放字
HEART_W = 7
_seen = {}                            # 已写盘的值, 只在用户改出新值时才写

# ---- 像素画: 字符画 -> 像素数组(0 即黑, 屏幕本来就是黑的, 当透明用) ----
# 两个角色各 12x12, 一边一个: 星之卡比(他喜欢) 与 ゆきお/Yukio(她喜欢, A&M サンワ 的
# "天空の非公認キャラクター")。Yukio 通体纯白, 只有两道眼缝、一点鼻子和一张小嘴 —
# 正好是这种低分辨率最吃得消的造型: 白底大头 + 三处黑, 12px 也还认得出。
# 两只都是照着印象手画的致敬, 不是官方素材。
KIRBY = """
...PPPPPP...
.PPPPPPPPPP.
PPPPPPPPPPPP
PPWWPPPPWWPP
PPkkPPPPkkPP
PPkkPPPPkkPP
BBPPPkkPPPBB
PPPPPPPPPPPP
.PPPPPPPPPP.
..PPPPPPPP..
.RRRR..RRRR.
.RRRR..RRRR.
"""
YUKIO = """
...WWWWWW...
.WWWWWWWWWW.
WWWWWWWWWWWs
WWkkWWWWkkWs
WWWWWWWWWWWs
WWWWWkkWWWWs
WWWWWWWWWWss
.WWWWWWWWss.
..WWWWWsss..
...WWWWss...
.WWWWWWWWss.
.WWWWWWWWss.
..WWWWWWss..
...WW..ss...
"""
HEART = """
.HH.HH.
HHHHHHH
HHHHHHH
.HHHHH.
..HHH..
...H...
"""
# 卡比: P 粉身 W 白高光 k 眼和嘴(不点亮) B 腮红 R 红脚
# 眼和嘴用"不点亮"而不是深色: 在这块屏上深蓝 #24325E 和深红 #C03050 都糊成一团脏色,
# 黑洞反而是唯一读得出的五官。腮红也从轮廓边挪进脸里, 挪之前根本看不出是腮红。
# Yukio: W 纯白身体 k 脸上的黑(其实就是不点亮, 屏幕本来是黑的)
PALETTE = {"P": 0xFFAEC9, "W": 0xFFFFFF, "k": 0, "B": 0xFF5C8A, "R": 0xE23A3A, "H": 0xFF3D6E}
# 纯白 0xFFFFFF 在这块 LED 上会晕开, 把眼缝和嘴糊住(实拍看得很清楚); 压一档到冷白就不晕了。
# 同理眼睛画两行高: 一行高的缝会被相邻白点的光吃掉。
YUKIO_PALETTE = {"W": 0xE8E8F0, "s": 0x8A90A6, "k": 0}
PAIR_W = 12                           # 两个角色各占的宽度
PAIR_PAD = 2                          # 公仔与数字之间的缝; 没有它数字会贴到卡比脸上
PAIR_TEXT_X = PAIR_W + PAIR_PAD
PAIR_TEXT_W = SCREEN_W - 2 * PAIR_TEXT_X   # 中间留给数字的 24px = 4 个字


def sprite(art, x, y, palette=None):
    """字符画转一条 db 指令: [x, y, w, h, 行优先像素]。db 会连背景一起画, 用 0(黑)当透明。"""
    rows = art.strip("\n").split("\n")
    pal = palette or PALETTE
    w, h = len(rows[0]), len(rows)
    return {"db": [x, y, w, h, [pal.get(c, 0) for row in rows for c in row]]}


def parse_date(s):
    try:
        return datetime.date.fromisoformat(str(s).strip())
    except ValueError:
        return None


def two_names(names):
    """"AMY & BEN" -> ("AMY", "BEN"); 中间那个 & 在屏上画成爱心, 比写出来省 12px 又好看。
    一个名字时右边留空。"""
    parts = [p.strip() for p in str(names).split("&") if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return (parts[0] if parts else "US"), ""


def fit_names(a, b):
    """两个名字 + 爱心 + 两边 1px 缝放得进 52px 就写全名, 否则退回首字母(K + S)。"""
    if (len(a) + len(b)) * CHAR_W + HEART_W + 2 <= SCREEN_W:
        return a, b
    return a[:1], b[:1]


def screen(item, today, start, names):
    """一屏的内容(纯函数, 便于测试)。
    {"layout": names|pair|wide, "text": 主文字, "right": 右名(names), "color": 覆盖色或 None}
    pair = 卡比 + 数字 + 小樱; 数字长到 4 个字放不下时退回 wide(只留卡比, 右边 36px 放字)。"""
    if item == "names":
        a, b = fit_names(*two_names(names))
        return {"layout": "names", "text": a, "right": b, "color": None}
    if start is None:                      # 日期没填对: 提示去面板填, 但名字那屏照常轮播
        return {"layout": "wide", "text": "SET?", "right": "", "color": None}
    text = str((today - start).days)
    # 数字放得进中间 24px 就两个角色一边一个; 放不下(天数到五位数)就只留卡比, 用右边 36px
    layout = "pair" if len(text) * CHAR_W <= PAIR_TEXT_W else "wide"
    return {"layout": layout, "text": text, "right": "", "color": None}


def _text_el(content, x, color):
    return {"content": content, "fontHeight": 10, "x": x, "y": 3, "color": color}


def _mid(art):
    return (SCREEN_H - len(art.strip("\n").split("\n"))) // 2


def _centered(text, left, width):
    return left + max(0, (width - len(text) * CHAR_W) // 2)


def build_frame(spec, color, duration):
    """把 screen() 的结果画成一帧。三种版式见 screen() 的 docstring。"""
    text = spec["text"]
    color = spec["color"] or color
    if spec["layout"] == "names":         # 左名 + 爱心 + 右名, 整屏居中
        b = spec["right"]
        total = (len(text) + len(b)) * CHAR_W + HEART_W + 2
        x = max(0, (SCREEN_W - total) // 2)
        hx = x + len(text) * CHAR_W + 1
        els = [_text_el(text, x, color)]
        if b:
            els.append(_text_el(b, hx + HEART_W + 1, color))
        return {"duration": duration, "draw": [sprite(HEART, hx, 5)], "text": els}
    if spec["layout"] == "pair":          # 卡比 + 数字 + 小樱, 左右等宽等缝
        y = (SCREEN_H - PAIR_W) // 2
        draw = [sprite(KIRBY, 0, y), sprite(YUKIO, SCREEN_W - PAIR_W, y, YUKIO_PALETTE)]
        return {"duration": duration, "draw": draw,
                "text": [_text_el(text, _centered(text, PAIR_TEXT_X, PAIR_TEXT_W), color)]}
    draw = [sprite(KIRBY, 1, 1)]          # 数字太长: 只留卡比, 右边 36px 放字
    return {"duration": duration, "draw": draw,
            "text": [_text_el(text, _centered(text, TEXT_X, TEXT_W), color)]}


def _current(options):
    """读选项, 顺手把改动写回配置; 返回 (日期, 名字, 颜色)。"""
    for key in ("date", "names"):
        raw = str(options.get(key, "")).strip()
        if key == "names":
            raw = core.ascii_upper(raw)
        elif not parse_date(raw):
            continue                       # 面板里打到一半的日期不写盘, 免得盖掉已存的好日期
        if raw and _seen.get(key) != raw:
            _seen[key] = raw
            _remember(key, raw)
    return (parse_date(options.get("date", OPTIONS[1]["default"])),
            core.ascii_upper(str(options.get("names", OPTIONS[0]["default"]))),
            options.get("color", OPTIONS[2]["default"]))


def frame_for(item, interval):
    """默认循环与面板"推一次"都走这里: 一屏静止 interval 秒。"""
    start, names, color = _current(_opts())
    spec = screen(item, datetime.date.today(), start, names)
    label = f"{spec['text']} <3 {spec['right']}" if spec["layout"] == "names" else spec["text"]
    return build_frame(spec, color, interval), f"anniv: {label}"


def run_loop(**kw):
    """默认循环不把 options 交给 frame_for; 这里先记下(面板给的是实时 dict), 再交给默认轮播。"""
    global _live
    _live = kw.get("options") or {}
    core.run_loop(sys.modules[__name__], **kw)


_live = None


def _opts():
    return _live if _live is not None else {o["key"]: o["default"] for o in OPTIONS}


if __name__ == "__main__":
    core.standalone(sys.modules[__name__])
