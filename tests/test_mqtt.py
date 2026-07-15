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


if __name__ == "__main__":
    unittest.main()
