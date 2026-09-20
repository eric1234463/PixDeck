#!/usr/bin/env python3
"""pixbar_panel.py — 像素时钟控制台（本地控制服务器, 纯标准库）

跑在 127.0.0.1: 自动发现 plugins/ 下所有插件, 用网页统一开关、看设备状态、手动推一帧。
浏览器只跟本服务器通信(同源), 服务器管线程 + 转发设备 API。

用法:
  python3 pixbar_panel.py                 # 启动后浏览器开 http://127.0.0.1:8000
  python3 pixbar_panel.py --device <IP> --port 8000
"""
import argparse, ipaddress, json, os, re, socket, subprocess, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

import pixbar_core as core

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIST = os.path.join(HERE, "web", "dist")
CONFIG_PATH = os.path.join(HERE, ".pixbar.json")          # 本地配置(设备 IP 等), 不入库


def _load_config():
    try:
        with open(CONFIG_PATH) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_config(d):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


def load_device():
    """读取上次在界面里设置并记住的设备 IP; 没有则返回空串。"""
    return str(_load_config().get("device", ""))


def save_device(ip):
    """把界面设置的设备 IP 写到本地配置, 重启后仍记得。"""
    d = _load_config(); d["device"] = ip; _save_config(d)


TRANSPORT_KEYS = ("transport", "broker", "prefix", "mqtt_user", "mqtt_pass", "retain")


def load_transport():
    d = _load_config()
    return {"transport": d.get("transport", "http"), "broker": d.get("broker", ""),
            "prefix": d.get("prefix", ""), "mqtt_user": d.get("mqtt_user", ""),
            "mqtt_pass": d.get("mqtt_pass", ""), "retain": bool(d.get("retain", False))}


def save_transport(t):
    d = _load_config()
    for k in TRANSPORT_KEYS:
        if k in t:
            d[k] = t[k]
    _save_config(d)


def apply_transport(t):
    """把持久化配置套用到 core(mqtt 时拆 broker host:port)。"""
    host, _, port = str(t.get("broker", "")).partition(":")
    core.configure_transport(mode=t.get("transport", "http"), broker_host=host,
                             broker_port=int(port) if port.isdigit() else 1883,
                             prefix=t.get("prefix", ""), username=t.get("mqtt_user") or None,
                             password=t.get("mqtt_pass") or None, retain=bool(t.get("retain")))
    ensure_status_watch(t)                  # mqtt: 订阅设备 LWT, 重启时停插件


def valid_device(s):
    """校验设备地址: 必须是私网 IPv4(可带 :端口)。服务器会拿这个地址去发请求, 限制到私网
    可挡住把它设成元数据(169.254.169.254)/环回/公网地址做 SSRF。合法则返回规范化串, 否则空串。"""
    s = (s or "").strip()
    if not s:
        return ""
    host, sep, port = s.partition(":")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return ""
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local:
        return ""
    if sep and (not port.isdigit() or not (1 <= int(port) <= 65535)):
        return ""
    return s


def valid_broker(s):
    """校验 MQTT broker 地址: 私网 IPv4(可带端口), 且额外允许环回 —— 本机跑 broker(如
    127.0.0.1:1883 的 mosquitto)很常见。仍挡住公网/元数据(169.254.x)。broker 由本地用户在
    设置里自填、连接由本机发起, 不同于设备寻址, 允许环回是安全的。"""
    s = (s or "").strip()
    if not s:
        return ""
    host, sep, port = s.partition(":")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return ""
    if ip.version != 4 or not ip.is_private or ip.is_link_local:   # is_private 含环回; 只挡公网/链路本地
        return ""
    if sep and (not port.isdigit() or not (1 <= int(port) <= 65535)):
        return ""
    return s
_ALL = core.discover(os.path.join(HERE, "plugins"))
PLUGINS = {m.APP: m for m in _ALL if not getattr(m, "ATTACH", False)}        # 主信息流插件
ATTACH_TYPES = {m.APP: m for m in _ALL if getattr(m, "ATTACH", False)}       # 附属推送类型(模板)


def apply_option(optspec, options, key, value):
    """按 optspec 校验并写入一个选项。支持 type=text / number / 默认下拉。成功返回该选项规格, 否则 None。"""
    for o in optspec:
        if o["key"] != key:
            continue
        t = o.get("type")
        if t in ("text", "search"):                   # search: 存选中项的值串(lat|lon|显示名)
            options[key] = str(value)[:300]
        elif t == "number":
            try:
                v = int(float(value))
            except Exception:
                return None
            if o.get("min") is not None:
                v = max(o["min"], v)
            if o.get("max") is not None:
                v = min(o["max"], v)
            options[key] = v
        elif value in [c[0] for c in o.get("choices", [])]:
            options[key] = value
        else:
            return None
        return o
    return None


class Runner:
    """管理一个插件的后台线程: 开/关 + 环形日志缓冲。"""
    def __init__(self, mod):
        self.mod = mod
        self.thread = None
        self.stop = None
        self.active = False                 # 用户意图(开/关), UI 以此为准
        self.interval = getattr(mod, "DEFAULT_INTERVAL", 5)
        self.optspec = getattr(mod, "OPTIONS", [])
        self.options = {o["key"]: o["default"] for o in self.optspec}   # 当前选项(线程内实时读)
        self.log = []
        self.lock = threading.Lock()

    def set_option(self, key, value):
        o = apply_option(self.optspec, self.options, key, value)
        if not o:
            return False
        self._emit(f"{time.strftime('%H:%M:%S')}  {o['label']} = {self.options[key]}")
        return True

    def _emit(self, line):
        with self.lock:
            self.log.append(line)
            del self.log[:-30]

    def running(self):
        return self.active

    def start(self, device, interval):
        if self.active:
            return
        self.interval = interval
        self.active = True
        self.stop = threading.Event()
        self._emit(f"{time.strftime('%H:%M:%S')}  started ({device}, {interval}s)")
        loop = core.loop_callable(self.mod)
        self.thread = threading.Thread(
            target=loop,
            kwargs=dict(device=device, interval=interval, stop=self.stop, log=self._emit, options=self.options),
            daemon=True)
        self.thread.start()

    def set_interval(self, iv, device=None):
        """设置轮播间隔并持久化。运行中则软重启线程(不删设备组件, 无空白)以套用新间隔。"""
        iv = max(1, int(iv))
        self.interval = iv
        self._emit(f"{time.strftime('%H:%M:%S')}  间隔 = {iv}s")
        if self.active and device is not None:
            if self.stop:
                self.stop.set()
            if self.thread:
                self.thread.join(timeout=3)
            self.active = False
            self.start(device, iv)

    def stop_run(self, device=None):
        self.active = False
        if self.stop:
            self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)               # 先等线程退出, 防止它再推一帧重建组件
        core.clear_preempt(self.mod.APP)               # 解除附属对本组件的抢占
        ts = time.strftime("%H:%M:%S")
        if device:                                     # 再从设备删除该 DIY 组件(空 body=删除), 不残留末帧
            try:
                core.push(device, self.mod.APP, {}, force=True)   # force: 即便正被抢占也要删掉
                self._emit(f"{ts}  stopped & removed from device")
            except Exception as e:
                self._emit(f"{ts}  stopped (device remove fail: {e})")
        else:
            self._emit(f"{ts}  stopped")

    def push_once(self, device, idx):
        items = self.mod.ITEMS
        item = items[idx % len(items)]
        res = self.mod.frame_for(item, self.interval)
        ts = time.strftime("%H:%M:%S")
        if not res:
            self._emit(f"{ts}  no data: {item}"); return
        try:
            core.push(device, self.mod.APP, res[0]); self._emit(f"{ts}  (once) {res[1]}")
        except Exception as e:
            self._emit(f"{ts}  push fail: {e}")

    def snapshot(self):
        with self.lock:
            return {"name": getattr(self.mod, "NAME", self.mod.APP),
                    "group": getattr(self.mod, "GROUP", "其他"),
                    "desc": getattr(self.mod, "DESC", ""),
                    "running": self.running(), "interval": self.interval,
                    "optspec": self.optspec, "options": dict(self.options), "log": list(self.log)}


RUNNERS = {app: Runner(mod) for app, mod in PLUGINS.items()}
_push_idx = {app: 0 for app in PLUGINS}


class Attachment:
    """一个附属推送的挂载实例: 附属类型 + 宿主 + 配置, 自带循环线程。"""
    def __init__(self, aid, mod, host):
        self.id = aid
        self.mod = mod                  # 附属类型模块
        self.host = host                # 宿主插件 APP 名
        self.optspec = getattr(mod, "OPTIONS", [])
        self.options = {o["key"]: o["default"] for o in self.optspec}
        self.active = False
        self.thread = None
        self.stop = None
        self.log = []
        self.lock = threading.Lock()

    def _emit(self, line):
        with self.lock:
            self.log.append(line)
            del self.log[:-30]

    def set_option(self, key, value):
        o = apply_option(self.optspec, self.options, key, value)
        if not o:
            return False
        self._emit(f"{time.strftime('%H:%M:%S')}  {o['label']} = {self.options[key]}")
        return True

    def running(self):
        return self.active

    def start(self, device):
        if self.active:
            return
        loop = getattr(self.mod, "attach_loop", None)
        if loop is None:                # 附属类型必须提供 attach_loop
            self._emit(f"{time.strftime('%H:%M:%S')}  错误: 附属类型缺少 attach_loop")
            return
        self.active = True
        self.stop = threading.Event()
        self._emit(f"{time.strftime('%H:%M:%S')}  started -> 宿主 {self.host}")

        def inject(frame, duration):    # 只在宿主运行时插播; 设备出错也不让线程挂掉
            hr = RUNNERS.get(self.host)
            if not hr or not hr.running():
                self._emit(f"{time.strftime('%H:%M:%S')}  跳过(宿主 {self.host} 未运行)")
                return
            try:
                core.inject(device, self.host, frame, duration)
            except Exception as e:
                self._emit(f"{time.strftime('%H:%M:%S')}  push fail: {e}")

        self.thread = threading.Thread(
            target=loop,
            kwargs=dict(device=device, host=self.host, options=self.options,
                        stop=self.stop, log=self._emit, inject=inject),
            daemon=True)
        self.thread.start()

    def stop_run(self):
        self.active = False
        if self.stop:
            self.stop.set()
        self._emit(f"{time.strftime('%H:%M:%S')}  stopped")

    def snapshot(self):
        with self.lock:
            return {"id": self.id, "type": self.mod.APP,
                    "typeName": getattr(self.mod, "NAME", self.mod.APP),
                    "host": self.host, "running": self.running(),
                    "optspec": self.optspec, "options": dict(self.options), "log": list(self.log)}


ATTACHMENTS = {}                        # id -> Attachment
_attach_seq = [0]
STATE_LOCK = threading.Lock()           # 保护跨请求/线程共享的 ATTACHMENTS / _attach_seq / _push_idx
_pushonce_at = {}                       # app -> 最近一次"推一次"的 monotonic(短时豁免对账)
PUSHONCE_GRACE = 30                     # 推一次后保留组件的秒数


def attach_types_info():
    return [{"type": m.APP, "name": getattr(m, "NAME", m.APP), "desc": getattr(m, "DESC", "")}
            for m in ATTACH_TYPES.values()]


def reconcile(device, apps):
    """删除设备上'未运行且非近期预览'的本工具组件, 使 DIY 组件始终 = 真正开着的插件。
    返回对账后仍在设备上的组件名列表。"""
    now = time.monotonic()
    kept = []
    for name in apps:
        stale = (name in PLUGINS and not RUNNERS[name].running()
                 and now - _pushonce_at.get(name, 0) > PUSHONCE_GRACE)
        if stale:
            try:
                core.push(device, name, {}, force=True)
                continue
            except Exception:
                pass
        kept.append(name)
    return kept


def geocode_search(q):
    """城市搜索: 中文自动转拼音(open-meteo 大城市主名是拼音, 直接搜中文只匹配到同名小地方),
    保留 open-meteo 的相关性排序(大城市通常排第一), 仅去重。
    需要 pypinyin 才能用中文搜大城市(可选: pip install pypinyin); 没装则退回原文搜索。"""
    q = (q or "").strip()
    if not q:
        return []
    name = q
    if any("一" <= c <= "鿿" for c in q):       # 含中文 -> 转拼音
        try:
            from pypinyin import lazy_pinyin
            name = "".join(lazy_pinyin(q))
        except Exception:
            pass
    try:
        g = json.load(urllib.request.urlopen(
            f"https://geocoding-api.open-meteo.com/v1/search?name={quote(name)}&count=20&language=zh", timeout=6))
    except Exception:
        return []
    # 只保留县级市以上: GeoNames 行政驻地(首都/省会/地级/县级)或人口>=3万; 丢掉村镇/机场等
    keep_codes = {"PPLC", "PPLA", "PPLA2", "PPLA3", "PPLG"}
    out, seen = [], set()
    for r in g.get("results", []):
        if r.get("feature_code") not in keep_codes and (r.get("population") or 0) < 30000:
            continue
        disp = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
        if disp in seen:
            continue
        seen.add(disp)
        out.append({"label": disp, "value": f"{r['latitude']}|{r['longitude']}|{disp}"})
    return out[:6]


def device_get(device, path, timeout=3):
    try:
        with urllib.request.urlopen(f"http://{device}{path}", timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


STATIC_TYPES = {".js": "application/javascript", ".css": "text/css",
                ".png": "image/png", ".html": "text/html; charset=utf-8",
                ".svg": "image/svg+xml", ".woff2": "font/woff2",
                ".json": "application/json", ".map": "application/json", ".ico": "image/x-icon"}


def device_status(device):
    base = device_get(device, "/getBase")
    return {
        "online": base is not None,
        "base": base or {},
        "apps": device_get(device, "/api/customList") or {},
        "config": device_get(device, "/getConfig") or {},
    }


# ---- 设备重启看门狗 ----
# 组件在设备上"曾出现过又消失" = 设备重启(或用户手删)。此时停掉插件, 画面留在设备内建 app,
# 等用户手动再开; 否则插件下一帧会把 DIY 组件重建, 画面立刻被抢回去。
# 只判"曾见过"的组件: 插件刚开还没推出第一帧(或 frame_for 暂时无数据)时不会被误停。
WATCHDOG_PERIOD = 5                     # 轮询 customList 的间隔秒数
WATCHDOG_MISS = 2                       # 连续缺席几次才停(抗 wifi 抖动/单次请求失败)
UNREACHABLE_STOP = 6                    # 连续不可达多少次(x WATCHDOG_PERIOD 秒)后停插件
_seen_on_device = set()                 # 确认过在设备上出现的组件名
_miss = {}                              # app -> 连续缺席次数
_unreachable = [0]                      # 连续不可达次数
_autostopped = {}                       # 因失联被自动停的插件 app -> interval(用户意图仍是"开")


def stop_for_restart(reason, apps=None):
    """设备重启后停掉运行中的插件: 不再推帧, 画面留在设备内建 app, 等用户手动再开。"""
    for app in (apps if apps is not None else list(RUNNERS)):
        r = RUNNERS[app]
        _seen_on_device.discard(app)
        _miss.pop(app, None)
        if r.running():
            r._emit(f"{time.strftime('%H:%M:%S')}  {reason}, 停止插件")
            r.stop_run()                    # 不传 device: 组件已不在设备上, 无需再推删除


def resume_autostopped(device):
    """失联期间被自动停掉的插件: 设备回来就自动开回去 —— 用户的意图本来就是"开", 是网络插手。
    设备重启导致的停止不走这里: 那种情况画面已经还给设备, 由用户决定何时抢回来。"""
    for app, iv in list(_autostopped.items()):
        del _autostopped[app]
        RUNNERS[app]._emit(f"{time.strftime('%H:%M:%S')}  设备回来了, 自动恢复")
        RUNNERS[app].start(device, iv)


def watchdog_tick(device):
    """对比设备组件列表与运行中的插件, 停掉组件已消失的那些。"""
    cl = device_get(device, "/api/customList")
    if cl is None:                      # 设备不可达: 不判组件消失(离线 != 重启)
        _unreachable[0] += 1
        if _unreachable[0] == UNREACHABLE_STOP:      # 只在跨过阈值那一刻动手
            _autostopped.update({a: r.interval for a, r in RUNNERS.items() if r.running()})
            if _autostopped:
                stop_for_restart(f"设备失联 {UNREACHABLE_STOP * WATCHDOG_PERIOD}s")
        return
    if _unreachable[0]:                 # 设备回来了
        _unreachable[0] = 0
        resume_autostopped(device)
    names = set(cl.get("apps", []))
    for app, r in RUNNERS.items():
        if not r.running():
            _seen_on_device.discard(app)
            _miss.pop(app, None)
        elif app in names:
            _seen_on_device.add(app)
            _miss[app] = 0
        elif app in _seen_on_device:
            _miss[app] = _miss.get(app, 0) + 1
            if _miss[app] >= WATCHDOG_MISS:
                stop_for_restart("设备重启: 组件已从设备消失", [app])


def watchdog_loop():
    while True:
        time.sleep(WATCHDOG_PERIOD)
        try:
            if Handler.device:
                watchdog_tick(Handler.device)
            maybe_rediscover()          # 地址空/连不上时自愈(换网段、DHCP 续约)
        except Exception:
            pass


# ---- 设备地址自动发现 (ARP) ----
# 换网段/DHCP 续约后设备 IP 会变。不能靠 MQTT 发现: <prefix>/status 之类的信号都要求设备
# 已经连上正确的 broker —— 而 broker 就跑在本机, 本机 IP 一变设备就连不上, 什么都收不到。
# ARP 是二层, 不需要设备连 broker、也不需要它先跟我们通信: 先向本网段每个地址发一个 UDP
# 空包(内核为送出这包必须先 ARP 解析), 再按 MAC 末四位认出设备。前提是同网段且 AP 未开
# 客户端隔离。SHORTCUT: 只认 MAC 末四位; 同网段撞尾四位的概率极低, 命中后还用 /getBase 复核。
DISCOVER_COOLDOWN = 60                  # 两次扫描之间的最短间隔(秒)
DISCOVER_CAP = 1024                     # 单次最多探多少个地址(挡住大网段)
_last_discover = [0.0]


def device_mac_suffix(prefix):
    """设备 prefix 末段就是 MAC 末四位(ulanzi_a2fa -> a2fa)。拿不到则空串。"""
    tail = str(prefix or "").rsplit("_", 1)[-1].lower()
    return tail if re.fullmatch(r"[0-9a-f]{4}", tail) else ""


def _arp_find(suffix4):
    """在系统 ARP 表里找 MAC 末四位匹配的地址。"""
    try:
        out = subprocess.run(["arp", "-an"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    for ip, mac in re.findall(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-f:]{11,17})", out, re.I):
        if mac.replace(":", "").lower().endswith(suffix4):
            return ip
    return ""


def _own_net():
    """本机所在网段(非环回的第一个 IPv4)。取不到返回 None。"""
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    for ip, mask in re.findall(r"inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-f]+)", out):
        if ip.startswith("127."):
            continue
        return ipaddress.ip_network(f"{ip}/{bin(int(mask, 16)).count('1')}", strict=False)
    return None


def _prime_arp(net):
    """向网段内地址各发一个 UDP 空包, 逼内核把它们 ARP 解析进表。不等回应。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(False)
    for n, h in enumerate(net.hosts()):
        if n >= DISCOVER_CAP:
            break
        try:
            s.sendto(b"", (str(h), 9))          # discard 端口: 对方不必回应
        except OSError:
            pass
    s.close()


def discover_device(prefix):
    """按 prefix 里的 MAC 末四位找出设备 IP, 并用 /getBase 复核。找不到返回空串。"""
    suffix = device_mac_suffix(prefix)
    if not suffix:
        return ""
    for attempt in (1, 2):
        ip = _arp_find(suffix)
        if ip and valid_device(ip) and device_get(ip, "/getBase"):
            return ip
        if attempt == 1:                        # ARP 表里没有: 扫一遍网段再看
            net = _own_net()
            if net is None:
                return ""
            _prime_arp(net)
            time.sleep(2)
    return ""


def maybe_rediscover():
    """设备地址为空或连不上时重新发现并写回配置(冷却 DISCOVER_COOLDOWN 秒)。"""
    dev = Handler.device
    if dev and device_get(dev, "/getBase"):
        return
    if time.monotonic() - _last_discover[0] < DISCOVER_COOLDOWN:
        return
    _last_discover[0] = time.monotonic()
    ip = discover_device(load_transport().get("prefix", ""))
    if not ip or ip == dev:
        return
    Handler.device = ip
    save_device(ip)
    if dev:                                     # 插件线程里握的是旧地址, 停掉等用户重开
        stop_for_restart(f"设备地址已变为 {ip}")


# ---- MQTT 模式的重启检测 ----
# mqtt 模式下设备 IP 常常连不上(换网段/AP 隔离), 上面的 customList 看门狗形同失效。
# 设备自己会在 <prefix>/status 上报 online/offline(LWT), 订阅它即可:
#   retained 的 online = 订阅瞬间 broker 补发的"当前状态", 不是事件, 必须忽略;
#   非 retained 的 online = 设备刚连上来 = 重启过 -> 停插件。
_status_sub = None


def on_device_status(payload, retained):
    if retained or payload.strip() != "online":
        return
    stop_for_restart("设备重启(mqtt 上线)")


def ensure_status_watch(t):
    """按当前传输配置建立/关闭 <prefix>/status 订阅。传输设置一改就重建。"""
    global _status_sub
    if _status_sub:
        _status_sub.close()
        _status_sub = None
    host, _, port = str(t.get("broker", "")).partition(":")
    prefix = str(t.get("prefix", "")).strip("/")
    if t.get("transport") != "mqtt" or not host or not prefix:
        return
    import pixbar_mqtt
    _status_sub = pixbar_mqtt.MqttSubscriber(
        host, int(port) if port.isdigit() else 1883, f"{prefix}/status", on_device_status,
        username=t.get("mqtt_user") or None, password=t.get("mqtt_pass") or None)


class Handler(BaseHTTPRequestHandler):
    device = ""                          # 设备 IP: 启动时从配置载入, 或在界面齿轮里设置

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def _host_ok(self):
        """只接受 Host 指向本机的请求。防 DNS-rebinding: 即便恶意域名解析到 127.0.0.1,
        浏览器仍会带上该域名作 Host, 这里据此拒绝, 不让外部网页驱动本地 API。"""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("", "127.0.0.1", "localhost", "::1")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, json.dumps({"error": "forbidden host"}))
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(WEB_DIST, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/api/status":
            st = device_status(Handler.device)
            if st.get("online"):                       # 对账: 清掉未运行的残留组件
                apps = (st.get("apps") or {}).get("apps", [])
                st["apps"]["apps"] = reconcile(Handler.device, apps)
            st["device"] = Handler.device
            st["order"] = list(RUNNERS.keys())
            st["runners"] = {n: r.snapshot() for n, r in RUNNERS.items()}
            st["attachTypes"] = attach_types_info()
            with STATE_LOCK:                       # 防与并发的 attach 增删争用 → "dict changed size"
                att_items = list(ATTACHMENTS.items())
            st["attachments"] = {aid: a.snapshot() for aid, a in att_items}
            _t = load_transport()
            st["transport"] = {"transport": _t["transport"], "broker": _t["broker"],
                               "prefix": _t["prefix"], "retain": _t["retain"],
                               "hasAuth": bool(_t["mqtt_user"])}
            return self._send(200, json.dumps(st))
        if u.path == "/api/geocode":                  # 城市搜索(中文转拼音 + 按人口排序 + 去重)
            q = (parse_qs(u.query).get("q") or [""])[0]
            return self._send(200, json.dumps({"results": geocode_search(q)}))
        # SPA 构建产物(/assets/* 等), 仅从 web/dist 服务
        rel = u.path.lstrip("/")
        ext = os.path.splitext(rel)[1]
        if rel and ".." not in rel and ext in STATIC_TYPES:
            root = os.path.realpath(WEB_DIST)
            fp = os.path.realpath(os.path.join(WEB_DIST, rel))
            if os.path.isfile(fp) and os.path.commonpath([fp, root]) == root:
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), STATIC_TYPES[ext])
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, json.dumps({"error": "forbidden host"}))
        u = urlparse(self.path)
        q = parse_qs(u.query)
        app = (q.get("app") or [""])[0]
        if u.path == "/api/device":
            cand = valid_device((q.get("ip") or [""])[0])
            if not cand:
                return self._send(400, json.dumps({"error": "需要私网 IPv4 地址(可带端口), 如 192.168.1.50"}))
            Handler.device = cand
            save_device(cand)                     # 记住, 重启后仍生效
            return self._send(200, json.dumps({"device": cand}))
        if u.path == "/api/transport":
            mode = (q.get("mode") or ["http"])[0]
            if mode not in ("http", "mqtt"):
                return self._send(400, json.dumps({"error": "mode must be http|mqtt"}))
            broker = (q.get("broker") or [""])[0]
            if mode == "mqtt" and not valid_broker(broker):
                return self._send(400, json.dumps({"error": "broker 需私网 IPv4(可带端口, 允许 127.0.0.1)"}))
            t = {"transport": mode, "broker": broker, "prefix": (q.get("prefix") or [""])[0],
                 "mqtt_user": (q.get("user") or [""])[0], "mqtt_pass": (q.get("pass") or [""])[0],
                 "retain": (q.get("retain") or ["0"])[0] == "1"}
            save_transport(t)
            apply_transport(t)
            return self._send(200, json.dumps({"ok": True, "transport": mode}))
        # ---- 画板整屏推送: body 为 JSON {pixels:[832], duration?} ----
        if u.path == "/api/canvas/push":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > 65536:                 # 整屏 832 像素 JSON 远小于此; 防空体/超大体
                    raise ValueError("bad length")
                payload = json.loads(self.rfile.read(n))
            except Exception:
                return self._send(400, json.dumps({"ok": False, "error": "bad json"}))
            pixels = payload.get("pixels")
            if not isinstance(pixels, list) or len(pixels) != 52 * 16:
                return self._send(400, json.dumps({"ok": False, "error": "pixels must be length 832"}))
            try:
                flat = [int(p) & 0xFFFFFF for p in pixels]
                duration = max(1, min(300, int(float(payload.get("duration") or 10))))  # 钳到 1..300s
            except Exception:
                return self._send(400, json.dumps({"ok": False, "error": "bad pixel/duration value"}))
            frame = core.bitmap_frame(flat, w=52, h=16, duration=duration)
            try:
                core.push(Handler.device, "canvas", frame, force=True)
                return self._send(200, json.dumps({"ok": True}))
            except Exception as e:
                return self._send(200, json.dumps({"ok": False, "error": str(e)}))
        # ---- 附属推送挂载 ----
        if u.path == "/api/attach/add":
            host = (q.get("host") or [""])[0]; atype = (q.get("type") or [""])[0]
            if host not in RUNNERS or atype not in ATTACH_TYPES:
                return self._send(400, json.dumps({"error": "bad host/type"}))
            with STATE_LOCK:
                _attach_seq[0] += 1; aid = f"{atype}-{_attach_seq[0]}"
                ATTACHMENTS[aid] = Attachment(aid, ATTACH_TYPES[atype], host)
                snap = ATTACHMENTS[aid].snapshot()
            return self._send(200, json.dumps(snap))
        if u.path in ("/api/attach/remove", "/api/attach/toggle", "/api/attach/option"):
            a = ATTACHMENTS.get((q.get("id") or [""])[0])
            if not a:
                return self._send(400, json.dumps({"error": "unknown attachment"}))
            if u.path == "/api/attach/remove":
                a.stop_run()
                with STATE_LOCK:
                    ATTACHMENTS.pop(a.id, None)
                return self._send(200, json.dumps({"ok": True}))
            if u.path == "/api/attach/toggle":
                on = (q.get("on") or ["0"])[0] == "1"
                a.start(Handler.device) if on else a.stop_run()
                return self._send(200, json.dumps(a.snapshot()))
            key = (q.get("key") or [""])[0]; value = (q.get("value") or [""])[0]
            a.set_option(key, value)
            return self._send(200, json.dumps(a.snapshot()))
        if app not in RUNNERS:
            return self._send(400, json.dumps({"error": "unknown app"}))
        r = RUNNERS[app]
        if u.path == "/api/toggle":
            on = (q.get("on") or ["0"])[0] == "1"
            try:
                interval = int((q.get("interval") or [str(r.interval)])[0])
            except (ValueError, TypeError):
                return self._send(400, json.dumps({"error": "bad interval"}))
            interval = max(1, min(86400, interval))    # 钳到合理范围, 防异常值
            _autostopped.pop(app, None)               # 手动操作覆盖"失联自动恢复"的意图
            r.start(Handler.device, interval) if on else r.stop_run(Handler.device)
            return self._send(200, json.dumps(r.snapshot()))
        if u.path == "/api/interval":
            try:
                iv = int(float((q.get("interval") or ["5"])[0]))
            except (ValueError, TypeError):
                return self._send(400, json.dumps({"error": "bad interval"}))
            r.set_interval(max(1, min(86400, iv)), Handler.device)
            return self._send(200, json.dumps(r.snapshot()))
        if u.path == "/api/pushonce":
            with STATE_LOCK:
                idx = _push_idx[app]; _push_idx[app] = idx + 1
            r.push_once(Handler.device, idx)
            _pushonce_at[app] = time.monotonic()       # 短时豁免对账, 让预览帧留得住
            return self._send(200, json.dumps(r.snapshot()))
        if u.path == "/api/option":
            key = (q.get("key") or [""])[0]; value = (q.get("value") or [""])[0]
            r.set_option(key, value)
            return self._send(200, json.dumps(r.snapshot()))
        return self._send(404, json.dumps({"error": "not found"}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default=None, help="设备 IP(可选; 不填则用上次界面里设置的, 或留空在网页里填)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--start", default="", help="启动后自动开启的插件(逗号分隔), 如 --start agent")
    args = ap.parse_args()
    raw = args.device if args.device is not None else load_device()
    Handler.device = valid_device(raw)       # 私网 IPv4 校验; 非法则视为未设置
    if raw and not Handler.device:
        print(f"警告: 设备地址 {raw!r} 不是有效私网 IPv4, 已忽略 — 请在网页里填")
    if args.device is not None and Handler.device:
        save_device(Handler.device)          # 命令行显式指定且合法时也记住
    apply_transport(load_transport())
    # 启动时清掉设备上本工具插件的残留组件(都处于"未运行"状态), 使 DIY 组件显示与开关一致
    if Handler.device:
        cl = device_get(Handler.device, "/api/customList")
        for name in (cl or {}).get("apps", []):
            if name in PLUGINS:
                try:
                    core.push(Handler.device, name, {}, force=True)
                except Exception:
                    pass
    for name in [x.strip() for x in args.start.split(",") if x.strip()]:
        if name in RUNNERS:
            RUNNERS[name].start(Handler.device, RUNNERS[name].mod.DEFAULT_INTERVAL)
        else:
            print(f"警告: --start {name} 不是已发现的插件, 已忽略")
    threading.Thread(target=watchdog_loop, daemon=True).start()   # 设备重启 -> 停掉插件, 不抢回画面
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"pixbar_panel -> http://127.0.0.1:{args.port}  (device {Handler.device or '未设置 — 在网页里填'})")
    print(f"plugins: {', '.join(RUNNERS) or '(none)'}")
    print("Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        for r in RUNNERS.values():
            r.stop_run()
        print("\nstopped")


if __name__ == "__main__":
    main()
