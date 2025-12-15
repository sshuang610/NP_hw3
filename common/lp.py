"""
共用網路通訊模組 - Length-Prefixed JSON Protocol

本模組提供所有 Server/Client 使用的統一封包格式

協定格式:
+------------------+------------------+
| 4 bytes (長度)    | N bytes (JSON)   |
| Big-Endian       | UTF-8 編碼        |
+------------------+------------------+

使用方式:
    # 發送 JSON
    send_json(sock, {"type": "REQUEST", "data": {...}})
    
    # 接收 JSON  
    response = recv_json(sock)

支援的最大封包: 4MB (用於遊戲檔案傳輸)
"""
import json
import socket
import struct
import contextlib
from typing import Any, Dict

# ============================================================
# 常數定義
# ============================================================
# 最大封包大小 (4MB，用於傳輸遊戲檔案)
MAX_FRAME = 4 * 1024 * 1024


# ============================================================
# 工具函數
# ============================================================
def find_free_port(start: int = 20000, end: int = 40000, host: str = "127.0.0.1") -> int:
    """
    在指定範圍內尋找可用的 port
    
    用途: 遊戲伺服器啟動時動態分配 port
    
    Args:
        start: 起始 port 號
        end: 結束 port 號
        host: 綁定的 IP 位址
    
    Returns:
        int: 可用的 port 號
    
    Raises:
        RuntimeError: 找不到可用的 port
    """
    for port in range(start, end + 1):
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError("在指定範圍內找不到可用的 port")


# ============================================================
# 底層網路讀寫函數
# ============================================================
def recv_all(sock: socket.socket, length: int) -> bytes:
    """
    接收指定長度的完整資料 (阻塞式)
    
    重要: TCP 是串流協定，recv() 可能只收到部分資料
          此函數會循環接收直到收滿指定長度
    
    Args:
        sock: TCP socket
        length: 要接收的位元組數
    
    Returns:
        bytes: 完整的資料
    
    Raises:
        ConnectionError: 連線中斷
    """
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ConnectionError("連線已關閉")
        data.extend(chunk)
    return bytes(data)


def send_all(sock: socket.socket, data: bytes) -> None:
    """
    傳送完整資料 (阻塞式)
    
    重要: send() 可能只傳送部分資料
          此函數會循環傳送直到全部傳完
    
    Args:
        sock: TCP socket
        data: 要傳送的資料
    
    Raises:
        ConnectionError: 連線中斷
    """
    total = 0
    while total < len(data):
        sent = sock.send(data[total:])
        if sent <= 0:
            raise ConnectionError("連線已關閉")
        total += sent


# ============================================================
# Length-Prefixed 封包函數
# ============================================================
def send_frame(sock: socket.socket, body: bytes) -> None:
    """
    傳送 length-prefixed 封包
    
    封包格式: [4 bytes 長度 (Big-Endian)] + [body]
    
    Args:
        sock: TCP socket
        body: 封包內容 (不含長度標頭)
    
    Raises:
        ValueError: 封包大小無效 (空或超過 MAX_FRAME)
    """
    if len(body) <= 0 or len(body) > MAX_FRAME:
        raise ValueError("封包大小無效")
    header = struct.pack("!I", len(body))  # 4 bytes, big-endian (!I)
    send_all(sock, header)
    send_all(sock, body)


def recv_frame(sock: socket.socket) -> bytes:
    """
    接收 length-prefixed 封包
    
    步驟:
    1. 先讀取 4 bytes 長度標頭
    2. 解析長度 (Big-Endian)
    3. 讀取指定長度的 body
    
    Returns:
        bytes: 封包內容 (不含長度標頭)
    
    Raises:
        ValueError: 封包大小無效
    """
    header = recv_all(sock, 4)
    (length,) = struct.unpack("!I", header)
    if length <= 0 or length > MAX_FRAME:
        raise ValueError("封包大小無效")
    return recv_all(sock, length)


# ============================================================
# JSON 高階 API (主要使用這兩個函數)
# ============================================================
def send_json(sock: socket.socket, obj: Dict[str, Any]) -> None:
    """
    傳送 JSON 物件
    
    這是最常用的發送函數，自動處理:
    - JSON 序列化 (緊湊格式，無空格)
    - UTF-8 編碼
    - Length-Prefixed 封裝
    
    使用範例:
        send_json(sock, {"type": "LOGIN", "username": "alice"})
    
    Args:
        sock: TCP socket
        obj: 要傳送的 dict
    """
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    send_frame(sock, body)


def recv_json(sock: socket.socket) -> Dict[str, Any]:
    """
    接收 JSON 物件
    
    這是最常用的接收函數，自動處理:
    - Length-Prefixed 解封裝
    - UTF-8 解碼
    - JSON 反序列化
    
    使用範例:
        response = recv_json(sock)
        if response.get("ok"):
            print("成功")
    
    Returns:
        dict: 解析後的 JSON 物件
    """
    body = recv_frame(sock)
    return json.loads(body.decode("utf-8"))
