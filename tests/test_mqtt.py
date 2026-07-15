"""MQTT 发布端单元测试(纯标准库 unittest)。

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
