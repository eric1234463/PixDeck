"""MQTT 传输 + 设备重启检测的单元测试(纯标准库 unittest)。

编码/解码对拍: encode_publish 出的字节能被自带的 decode_publish 解回同样的 topic/payload。
不依赖 mqtt_sniff.py(它是 gitignore 的本地工具)。
运行: python3 -m unittest tests.test_mqtt  (在仓库根目录)
"""
import importlib.util, os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import pixbar_mqtt as mq


class TestCodec(unittest.TestCase):
    def test_remlen_single_byte(self):
        self.assertEqual(mq._remlen(0), b"\x00")
        self.assertEqual(mq._remlen(127), b"\x7f")

    def test_remlen_multibyte(self):
        # 300 -> 0xAC 0x02 per MQTT spec
        self.assertEqual(mq._remlen(300), b"\xac\x02")

    def test_publish_roundtrip_ascii(self):
        pkt = mq.encode_publish("ulanzi_1bf6/custom/stock", '{"a":1}', qos=0, retain=False)
        topic, payload, retain = mq.decode_publish(pkt)
        self.assertEqual(topic, "ulanzi_1bf6/custom/stock")
        self.assertEqual(payload, b'{"a":1}')
        self.assertFalse(retain)

    def test_publish_roundtrip_retain_and_large(self):
        big = "x" * 500  # forces multi-byte remaining length
        pkt = mq.encode_publish("t/opic", big, qos=0, retain=True)
        topic, payload, retain = mq.decode_publish(pkt)
        self.assertEqual(topic, "t/opic")
        self.assertEqual(payload, big.encode())
        self.assertTrue(retain)

    def test_connect_has_protocol_name(self):
        pkt = mq.encode_connect("pixdeck", keepalive=0)
        self.assertEqual(pkt[0], 0x10)          # CONNECT type
        self.assertIn(b"MQTT", pkt)             # protocol name


import socket as _socket, threading as _threading


class _StubBroker:
    """极简 broker: 接一个连接, 回 CONNACK, 读一个 PUBLISH 并解码存下。"""
    def __init__(self):
        self.srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self.srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.got = []
        self.t = _threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        c, _ = self.srv.accept()
        self.srv.close()                          # 只收一个连接, 关掉监听 socket 免 ResourceWarning
        # read CONNECT: fixed header(1) + remlen + body
        c.recv(1); rl, _ = self._recv_remlen(c); self._recv_exact(c, rl)
        c.sendall(b"\x20\x02\x00\x00")            # CONNACK accepted
        b0 = c.recv(1)                            # PUBLISH fixed header byte
        rl, _ = self._recv_remlen(c)
        body = self._recv_exact(c, rl)
        self.got.append(mq.decode_publish(b0 + mq._remlen(rl) + body))
        c.close()

    def _recv_exact(self, c, n):
        buf = b""
        while len(buf) < n:
            buf += c.recv(n - len(buf))
        return buf

    def _recv_remlen(self, c):
        mult, val = 1, 0
        while True:
            b = c.recv(1)[0]
            val += (b & 0x7F) * mult
            if not (b & 0x80):
                return val, None
            mult *= 128


class TestPublisher(unittest.TestCase):
    def test_publish_reaches_broker(self):
        b = _StubBroker()
        pub = mq.MqttPublisher("127.0.0.1", b.port, client_id="t", retain=False)
        pub.publish("ulanzi_1bf6/custom/pet", '{"duration":5}')
        b.t.join(timeout=3)
        self.assertEqual(len(b.got), 1)
        topic, payload, retain = b.got[0]
        self.assertEqual(topic, "ulanzi_1bf6/custom/pet")
        self.assertEqual(payload, b'{"duration":5}')
        pub.close()


if __name__ == "__main__":
    unittest.main()


import json as _json
import pixbar_core as core


class _FakePub:
    def __init__(self):
        self.calls = []
    def publish(self, topic, payload, retain=None):
        self.calls.append((topic, payload))
    def close(self):
        pass


class TestPushRouting(unittest.TestCase):
    def tearDown(self):
        core.configure_transport(mode="http")     # reset global state

    def test_mqtt_mode_publishes_to_custom_topic(self):
        fake = _FakePub()
        core._transport.update({"mode": "mqtt", "publisher": fake, "prefix": "ulanzi_1bf6"})
        frame = {"duration": 5, "text": []}
        core.push("192.168.1.9", "stock", frame)
        self.assertEqual(fake.calls, [("ulanzi_1bf6/custom/stock", _json.dumps(frame))])

    def test_mqtt_preempt_still_blocks_non_force(self):
        import time
        fake = _FakePub()
        core._transport.update({"mode": "mqtt", "publisher": fake, "prefix": "p"})
        core._preempt["stock"] = time.monotonic() + 100     # host preempted
        core.push("192.168.1.9", "stock", {"x": 1})         # non-force -> dropped
        self.assertEqual(fake.calls, [])
        core._preempt.pop("stock", None)


import importlib.util as _ilu


def _load_panel():
    path = os.path.join(ROOT, "pixbar_panel.py")
    spec = _ilu.spec_from_file_location("pixbar_panel", path)
    m = _ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestPanelTransportConfig(unittest.TestCase):
    def setUp(self):
        self.panel = _load_panel()
        self.tmp = os.path.join(ROOT, ".pixbar_test.json")
        self.panel.CONFIG_PATH = self.tmp

    def tearDown(self):
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    def test_transport_roundtrip_preserves_device(self):
        self.panel.save_device("192.168.1.9")
        self.panel.save_transport({"transport": "mqtt", "broker": "192.168.1.5:1883",
                                   "prefix": "ulanzi_1bf6", "retain": True})
        self.assertEqual(self.panel.load_device(), "192.168.1.9")   # device untouched
        t = self.panel.load_transport()
        self.assertEqual(t["transport"], "mqtt")
        self.assertEqual(t["prefix"], "ulanzi_1bf6")
        self.assertTrue(t["retain"])

    def test_valid_broker_allows_loopback_and_private_blocks_public(self):
        vb = self.panel.valid_broker
        self.assertEqual(vb("127.0.0.1:1883"), "127.0.0.1:1883")   # 本机 broker 允许(与 valid_device 不同)
        self.assertEqual(vb("192.168.1.5"), "192.168.1.5")         # 私网允许
        self.assertEqual(vb("8.8.8.8:1883"), "")                   # 公网挡住
        self.assertEqual(vb("169.254.169.254"), "")               # 元数据/链路本地挡住
        self.assertEqual(self.panel.valid_device("127.0.0.1"), "")  # 设备仍禁环回(不受影响)


class TestSubscribeCodec(unittest.TestCase):
    def test_subscribe_packet_shape(self):
        pkt = mq.encode_subscribe("ulanzi_a2fa/status")
        self.assertEqual(pkt[0], 0x82)                      # SUBSCRIBE type + QoS1 flags
        self.assertEqual(pkt[2:4], b"\x00\x01")             # packet id
        self.assertIn(b"ulanzi_a2fa/status", pkt)
        self.assertEqual(pkt[-1], 0)                        # requested QoS 0


class TestRestartDetection(unittest.TestCase):
    """设备重启检测的判定规则(不碰网络; 线程与 socket 由 test_mqtt 的真 broker 部分覆盖)。"""
    def setUp(self):
        self.panel = _load_panel()
        self.app = next(iter(self.panel.RUNNERS))
        self.r = self.panel.RUNNERS[self.app]
        self.r.active = True                    # 伪装成运行中, 不起真线程
        self.r.stop = self.r.thread = None
        self.stopped = []
        self.panel.stop_for_restart = lambda reason, apps=None: self.stopped.append((reason, apps))

    def test_retained_online_is_state_not_event(self):
        self.panel.on_device_status("online", True)         # 订阅瞬间 broker 补发的现状
        self.assertEqual(self.stopped, [])

    def test_offline_does_not_stop(self):
        self.panel.on_device_status("offline", False)       # 掉线 != 重启; 回来才算
        self.assertEqual(self.stopped, [])

    def test_live_online_stops_plugins(self):
        self.panel.on_device_status("online", False)        # 设备刚连上来 = 重启过
        self.assertEqual(len(self.stopped), 1)

    def test_watchdog_needs_seen_then_missing_twice(self):
        p = self.panel
        p.device_get = lambda *a, **k: {"apps": []}
        p.watchdog_tick("10.0.0.1"); p.watchdog_tick("10.0.0.1")
        self.assertEqual(self.stopped, [])                  # 从未出现过的组件不判(第一帧未推出)
        p.device_get = lambda *a, **k: {"apps": [self.app]}
        p.watchdog_tick("10.0.0.1")
        p.device_get = lambda *a, **k: {"apps": []}
        p.watchdog_tick("10.0.0.1")
        self.assertEqual(self.stopped, [])                  # 单次缺席 = wifi 抖动
        p.device_get = lambda *a, **k: None
        p.watchdog_tick("10.0.0.1")
        self.assertEqual(self.stopped, [])                  # 设备不可达: 不判, 也不累加
        self.assertEqual(p._miss[self.app], 1)
        p.device_get = lambda *a, **k: {"apps": []}
        p.watchdog_tick("10.0.0.1")
        self.assertEqual(len(self.stopped), 1)              # 连续两次缺席 -> 停
        self.assertEqual(self.stopped[0][1], [self.app])

    def test_stop_for_restart_clears_state_and_logs(self):
        p2 = _load_panel()                                  # 干净模块: 用真的 stop_for_restart
        app = self.app
        r = p2.RUNNERS[app]
        r.active = True
        r.stop = r.thread = None
        p2._seen_on_device.add(app)
        p2._miss[app] = 1
        p2.stop_for_restart("设备重启(mqtt 上线)")
        self.assertFalse(r.running())
        self.assertNotIn(app, p2._seen_on_device)
        self.assertNotIn(app, p2._miss)
        self.assertIn("设备重启(mqtt 上线)", r.log[-2])


class TestStatusWatchLifecycle(unittest.TestCase):
    def test_http_mode_and_missing_prefix_start_no_subscription(self):
        p = _load_panel()
        for t in ({"transport": "http", "broker": "10.0.0.2:1883", "prefix": "x"},
                  {"transport": "mqtt", "broker": "10.0.0.2:1883", "prefix": ""},
                  {"transport": "mqtt", "broker": "", "prefix": "x"}):
            p.ensure_status_watch(t)
            self.assertIsNone(p._status_sub, t)
