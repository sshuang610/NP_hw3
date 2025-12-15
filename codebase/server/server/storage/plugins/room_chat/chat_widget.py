"""
Room Chat Plugin - Client Widget
提供房間內群組聊天功能
"""
import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import socket
import json
import time
from typing import Optional, Callable, Dict, Any

class ChatWidget(ttk.Frame):
    """
    聊天 Widget，可嵌入到玩家客戶端的房間介面中
    """
    
    def __init__(self, parent, player_username: str, room_id: int, 
                 send_callback: Optional[Callable[[str], None]] = None,
                 **kwargs):
        super().__init__(parent, **kwargs)
        
        self.player_username = player_username
        self.room_id = room_id
        self.send_callback = send_callback
        self.messages = []
        
        self._build_ui()
        
    def _build_ui(self):
        """建立聊天介面"""
        # 標題
        header = ttk.Frame(self)
        header.pack(fill="x", padx=5, pady=2)
        ttk.Label(header, text="💬 房間聊天", font=("", 10, "bold")).pack(side="left")
        
        # 聊天訊息顯示區
        self.chat_display = scrolledtext.ScrolledText(
            self, 
            height=8, 
            width=30,
            wrap=tk.WORD,
            state="disabled",
            font=("", 9)
        )
        self.chat_display.pack(fill="both", expand=True, padx=5, pady=2)
        
        # 配置標籤樣式
        self.chat_display.tag_configure("system", foreground="gray", font=("", 9, "italic"))
        self.chat_display.tag_configure("me", foreground="blue")
        self.chat_display.tag_configure("other", foreground="green")
        self.chat_display.tag_configure("timestamp", foreground="gray", font=("", 8))
        
        # 輸入區
        input_frame = ttk.Frame(self)
        input_frame.pack(fill="x", padx=5, pady=5)
        
        self.message_var = tk.StringVar()
        self.message_entry = ttk.Entry(input_frame, textvariable=self.message_var)
        self.message_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.message_entry.bind("<Return>", lambda e: self.send_message())
        
        self.send_btn = ttk.Button(input_frame, text="發送", width=6, command=self.send_message)
        self.send_btn.pack(side="right")
        
        # 顯示歡迎訊息
        self._add_system_message("已加入房間聊天")
        
    def send_message(self):
        """發送聊天訊息"""
        message = self.message_var.get().strip()
        if not message:
            return
        
        # 清空輸入框
        self.message_var.set("")
        
        # 本地顯示
        self._add_message(self.player_username, message, is_me=True)
        
        # 透過回調發送到伺服器
        if self.send_callback:
            try:
                self.send_callback(message)
            except Exception as e:
                self._add_system_message(f"發送失敗: {e}")
    
    def receive_message(self, username: str, message: str, timestamp: Optional[str] = None):
        """接收其他玩家的訊息"""
        is_me = (username == self.player_username)
        self._add_message(username, message, is_me=is_me, timestamp=timestamp)
    
    def _add_message(self, username: str, message: str, is_me: bool = False, timestamp: Optional[str] = None):
        """添加訊息到顯示區"""
        self.chat_display.configure(state="normal")
        
        # 時間戳
        ts = timestamp or time.strftime("%H:%M")
        
        # 格式化訊息
        tag = "me" if is_me else "other"
        prefix = "你" if is_me else username
        
        self.chat_display.insert(tk.END, f"[{ts}] ", "timestamp")
        self.chat_display.insert(tk.END, f"{prefix}: ", tag)
        self.chat_display.insert(tk.END, f"{message}\n")
        
        self.chat_display.configure(state="disabled")
        self.chat_display.see(tk.END)
        
        # 保存訊息記錄
        self.messages.append({
            "username": username,
            "message": message,
            "timestamp": ts,
            "is_me": is_me
        })
    
    def _add_system_message(self, message: str):
        """添加系統訊息"""
        self.chat_display.configure(state="normal")
        self.chat_display.insert(tk.END, f"📢 {message}\n", "system")
        self.chat_display.configure(state="disabled")
        self.chat_display.see(tk.END)
    
    def player_joined(self, username: str):
        """玩家加入房間通知"""
        self._add_system_message(f"{username} 加入了房間")
    
    def player_left(self, username: str):
        """玩家離開房間通知"""
        self._add_system_message(f"{username} 離開了房間")
    
    def clear_messages(self):
        """清空所有訊息"""
        self.chat_display.configure(state="normal")
        self.chat_display.delete("1.0", tk.END)
        self.chat_display.configure(state="disabled")
        self.messages.clear()


# Plugin 接口函數 - 供主程式調用
def create_widget(parent, player_username: str, room_id: int, send_callback=None) -> ChatWidget:
    """
    創建聊天 Widget 的工廠函數
    
    Args:
        parent: 父級 Tkinter 容器
        player_username: 當前玩家使用者名稱
        room_id: 房間 ID
        send_callback: 發送訊息的回調函數
    
    Returns:
        ChatWidget 實例
    """
    return ChatWidget(parent, player_username, room_id, send_callback)


def get_plugin_info() -> Dict[str, Any]:
    """返回 Plugin 資訊"""
    return {
        "name": "Room Chat Plugin",
        "version": "1.0.0",
        "description": "在遊戲房間內提供群組聊天功能",
        "author": "System",
        "widget_class": ChatWidget,
        "create_widget": create_widget,
    }


# 測試用
if __name__ == "__main__":
    root = tk.Tk()
    root.title("Chat Widget Test")
    root.geometry("350x400")
    
    def mock_send(msg):
        print(f"Sending: {msg}")
        # 模擬收到其他人的訊息
        widget.receive_message("OtherPlayer", f"收到: {msg}")
    
    widget = ChatWidget(root, "TestPlayer", 1, send_callback=mock_send)
    widget.pack(fill="both", expand=True, padx=10, pady=10)
    
    # 模擬一些訊息
    widget.player_joined("Player2")
    widget.receive_message("Player2", "大家好！")
    
    root.mainloop()
