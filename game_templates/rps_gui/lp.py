import json
import socket
import struct
from typing import Any, Dict


MAX_FRAME = 1_048_576  # 1 MB safety limit


def recv_all(sock: socket.socket, length: int) -> bytes:
    buf = bytearray()
    while len(buf) < length:
        chunk = sock.recv(length - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def send_all(sock: socket.socket, data: bytes) -> None:
    view = memoryview(data)
    total = 0
    while total < len(view):
        sent = sock.send(view[total:])
        if sent <= 0:
            raise ConnectionError("socket closed during send")
        total += sent


def send_frame(sock: socket.socket, payload: bytes) -> None:
    if not payload:
        raise ValueError("empty payload")
    if len(payload) > MAX_FRAME:
        raise ValueError("frame too large")
    header = struct.pack("!I", len(payload))
    send_all(sock, header)
    send_all(sock, payload)


def recv_frame(sock: socket.socket) -> bytes:
    header = recv_all(sock, 4)
    (length,) = struct.unpack("!I", header)
    if length <= 0 or length > MAX_FRAME:
        raise ValueError("invalid frame length")
    return recv_all(sock, length)


def send_json(sock: socket.socket, obj: Dict[str, Any]) -> None:
    send_frame(sock, json.dumps(obj).encode("utf-8"))


def recv_json(sock: socket.socket) -> Dict[str, Any]:
    return json.loads(recv_frame(sock).decode("utf-8"))
