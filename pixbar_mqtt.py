#!/usr/bin/env python3
"""pixbar_mqtt.py — 最小 MQTT 3.1.1 发布端(纯标准库)。

把画面帧 JSON 以 PUBLISH(QoS 0) 发到 broker 的 <prefix>/custom/<app> topic。
编解码思路借鉴本地探查工具 mqtt_sniff.py, 但本文件自包含、不 import 它(后者不入库)。
MqttPublisher 发布帧; MqttSubscriber 订阅单个 topic(设备在线状态), 各用一条连接。
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
    if username is None:
        password = None                           # MQTT 3.1.1: 无 username 不得带 password,
                                                  # 否则 broker 视为协议错误直接断线(表现为 no CONNACK)
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


def encode_subscribe(topic, packet_id=1, qos=0):
    """SUBSCRIBE 包(单个 topic)。"""
    body = int(packet_id).to_bytes(2, "big") + _str(topic) + bytes([qos & 3])
    return b"\x82" + _remlen(len(body)) + body


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
        s = socket.create_connection((self.host, self.port), timeout=3)   # 短超时: broker 挂时不长时间卡住推送
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


class MqttSubscriber:
    """订阅单个 topic 的后台读取端: 每条消息回调 on_message(payload_str, retained)。

    retained 透传给调用方: broker 在订阅瞬间补发的保留消息是"当前状态", 不是新事件。
    keepalive=0 与发布端一致(告知 broker 不做保活, 故无需发 PINGREQ)。
    断线 RETRY 秒后重连重订; close() 后不再重连。
    """
    RETRY = 5

    def __init__(self, host, port, topic, on_message, client_id="pixdeck-sub",
                 username=None, password=None):
        self.host = host
        self.port = int(port or 1883)
        self.topic = topic
        self.on_message = on_message
        self.client_id = client_id
        self.username = username or None
        self.password = password or None
        self._sock = None
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _read_packet(self):
        """读一个完整 MQTT 包 -> (首字节, body bytes); 连接断开返回 None。"""
        b0 = _recv_exact(self._sock, 1)
        if not b0:
            return None
        n, mult = 0, 1
        while True:
            b = _recv_exact(self._sock, 1)
            if not b:
                return None
            n += (b[0] & 0x7F) * mult
            if not (b[0] & 0x80):
                break
            mult *= 128
        body = _recv_exact(self._sock, n) if n else b""
        if body is None:
            return None
        return b0[0], body

    def _run(self):
        while not self._stop.is_set():
            try:
                s = socket.create_connection((self.host, self.port), timeout=5)
                s.sendall(encode_connect(self.client_id, self.username, self.password, 0))
                ack = _recv_exact(s, 4)
                if not ack or (ack[0] >> 4) != 2:
                    raise OSError("no CONNACK")
                s.sendall(encode_subscribe(self.topic))
                s.settimeout(None)                      # 订阅后长期阻塞等消息
                self._sock = s
                while not self._stop.is_set():
                    pkt = self._read_packet()
                    if pkt is None:
                        break
                    b0, body = pkt
                    if b0 >> 4 != 3:                    # 只关心 PUBLISH; SUBACK 等忽略
                        continue
                    _t, payload, retained = decode_publish(bytes([b0]) + _remlen(len(body)) + body)
                    try:
                        self.on_message(payload.decode("utf-8", "replace"), retained)
                    except Exception:
                        pass                            # 回调出错不能弄断订阅
            except OSError:
                pass
            self._close()
            self._stop.wait(self.RETRY)

    def _close(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def close(self):
        self._stop.set()
        self._close()
