#!/usr/bin/env python3
"""pixbar_mqtt.py — 最小 MQTT 3.1.1 发布端(纯标准库)。

把画面帧 JSON 以 PUBLISH(QoS 0) 发到 broker 的 <prefix>/custom/<app> topic。
编解码思路借鉴本地探查工具 mqtt_sniff.py, 但本文件自包含、不 import 它(后者不入库)。
只发布, 不订阅。MqttPublisher 见文件后半部分。
"""
import socket, threading


def _remlen(n):
    """MQTT Remaining Length 变长编码。"""
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s):
    """MQTT UTF-8 字符串: 2 字节大端长度 + 内容。"""
    b = s.encode("utf-8")
    return len(b).to_bytes(2, "big") + b


def encode_connect(client_id, username=None, password=None, keepalive=0):
    """CONNECT 包。keepalive=0 表示不启用 broker 保活(我们靠出错重连)。"""
    flags = 0x02                                  # clean session
    if username is not None:
        flags |= 0x80
    if password is not None:
        flags |= 0x40
    vh = b"\x00\x04MQTT\x04" + bytes([flags]) + int(keepalive).to_bytes(2, "big")
    payload = _str(client_id)
    if username is not None:
        payload += _str(username)
    if password is not None:
        payload += _str(password)
    body = vh + payload
    return b"\x10" + _remlen(len(body)) + body


def encode_publish(topic, payload, qos=0, retain=False):
    """PUBLISH 包(QoS 0: 无 packet id)。payload 可为 str 或 bytes。"""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    header = 0x30 | (1 if retain else 0) | ((qos & 3) << 1)
    body = _str(topic) + payload
    return bytes([header]) + _remlen(len(body)) + body


def _read_remlen(data, i):
    mult, val = 1, 0
    while True:
        b = data[i]; i += 1
        val += (b & 0x7F) * mult
        if not (b & 0x80):
            return val, i
        mult *= 128


def decode_publish(data):
    """解析一个 QoS-0 PUBLISH 包 -> (topic, payload_bytes, retain)。仅供测试/对拍。"""
    assert data[0] >> 4 == 3, f"not a PUBLISH (type={data[0] >> 4})"
    retain = bool(data[0] & 0x01)
    qos = (data[0] >> 1) & 3
    rl, i = _read_remlen(data, 1)
    end = i + rl
    tlen = int.from_bytes(data[i:i + 2], "big"); i += 2
    topic = data[i:i + tlen].decode("utf-8"); i += tlen
    if qos > 0:
        i += 2
    return topic, data[i:end], retain


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


class MqttPublisher:
    """线程安全的 MQTT 发布端: 惰性连接 + 出错重连。QoS 0, 不发保活 PING。

    push 频率高时连接自然保持热; 空闲被 broker 断开后, 下次 publish 会重连。
    keepalive=0 通知 broker 不做保活, 避免空闲期被动断连。
    """
    def __init__(self, host, port=1883, client_id="pixdeck",
                 username=None, password=None, keepalive=0, retain=False):
        self.host = host
        self.port = int(port or 1883)
        self.client_id = client_id or "pixdeck"
        self.username = username or None
        self.password = password or None
        self.keepalive = keepalive
        self.retain = retain
        self._sock = None
        self._lock = threading.Lock()

    def _connect_locked(self):
        s = socket.create_connection((self.host, self.port), timeout=10)
        s.sendall(encode_connect(self.client_id, self.username, self.password, self.keepalive))
        ack = _recv_exact(s, 4)
        if not ack or (ack[0] >> 4) != 2:
            s.close()
            raise OSError("no CONNACK")
        self._sock = s

    def _close_locked(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def publish(self, topic, payload, retain=None):
        """发布一帧。失败自动重连重试一次; 仍失败则抛 OSError(由调用方记日志)。"""
        r = self.retain if retain is None else retain
        pkt = encode_publish(topic, payload, qos=0, retain=r)
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._sock is None:
                        self._connect_locked()
                    self._sock.sendall(pkt)
                    return
                except OSError:
                    self._close_locked()
                    if attempt == 2:
                        raise

    def close(self):
        with self._lock:
            self._close_locked()
