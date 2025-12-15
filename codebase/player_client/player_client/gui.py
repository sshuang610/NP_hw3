"""
玩家客戶端 GUI (Player Client)
使用 Tkinter 建構的圖形化遊戲大廳客戶端

功能模組:
- 帳號系統: 註冊/登入玩家帳號
- 遊戲商城 (P1): 瀏覽可用遊戲、查看評分/留言
- 下載系統 (P2): 下載遊戲到本機、管理本機遊戲庫
- 房間系統 (P3): 建立房間、加入房間、啟動遊戲
- 評分留言 (P4): 對已遊玩遊戲進行評分、撰寫評論
- Plugin 系統 (PL1-PL4): 購買、安裝、啟用聊天室 Plugin

主要 Class:
- PlayerApp: 主視窗，包含所有 UI 邏輯
- LobbyConnection: 與 Lobby Server 的網路連線封裝
- PlayerInfo: 玩家資訊 dataclass

網路通訊:
- 使用 Length-Prefixed JSON Protocol (common/lp.py)
- 連接到 Lobby Server (預設 port 23002)

作者: HW3 作業
"""
import base64
import hashlib
import json
import os
import socket
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import zipfile
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

from common.lp import recv_json, send_json


# 遊戲下載存放目錄
DOWNLOAD_ROOT = Path(__file__).resolve().parent / "downloads"
# Plugin 安裝目錄
PLUGIN_ROOT = Path(__file__).resolve().parent / "plugins"


@dataclass
class PlayerInfo:
    """玩家資訊，登入成功後由 Server 回傳"""
    id: int
    username: str


class LobbyConnection:
    """
    Lobby Server 連線管理
    - Thread-safe 的 socket 封裝
    - 提供 call() 方法進行 Request-Response 通訊
    """
    def __init__(self) -> None:
        self.sock: Optional[socket.socket] = None
        self.lock = threading.Lock()

    def connect(self, host: str, port: int) -> None:
        """建立 TCP 連線到 Lobby Server"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))
        with self.lock:
            self.sock = sock

    def close(self) -> None:
        """關閉連線"""
        with self.lock:
            if self.sock:
                self.sock.close()
                self.sock = None

    def call(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """發送 Request 並等待 Response (同步阻塞)"""
        with self.lock:
            if not self.sock:
                raise RuntimeError("connection not available")
            send_json(self.sock, payload)
            return recv_json(self.sock)


def hash_password(password: str) -> str:
    """密碼 SHA256 雜湊，不以明文傳輸"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def ensure_dir(path: Path) -> Path:
    """確保目錄存在，若不存在則建立"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_package(root: Path, game: Dict[str, Any], version: Dict[str, Any], payload: str, expected_sha256: Optional[str] = None) -> Path:
    """
    Save and extract a game package.
    Validates SHA256 hash if provided to ensure file integrity.
    """
    ensure_dir(root)
    game_slug = f"{game['id']}_{game['title'].replace(' ', '_')}"
    version_folder = ensure_dir(root / game_slug / "versions" / version["version_label"])
    package_path = version_folder / "package.zip"
    
    # Decode and validate data
    data = base64.b64decode(payload.encode("ascii"))
    
    # Verify SHA256 hash if provided
    if expected_sha256:
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(f"File integrity check failed: expected {expected_sha256[:16]}..., got {actual_sha256[:16]}...")
    
    # Write package file
    package_path.write_bytes(data)
    
    # Verify the zip file is valid before extracting
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            bad_file = zf.testzip()
            if bad_file:
                raise zipfile.BadZipFile(f"Corrupted file in archive: {bad_file}")
    except zipfile.BadZipFile as e:
        # Remove corrupted file
        package_path.unlink(missing_ok=True)
        raise ValueError(f"Downloaded package is corrupted: {e}")
    
    # Save metadata
    metadata = {
        "game": game,
        "version": version,
        "downloaded_at": datetime.now().isoformat(),
    }
    (version_folder / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    
    # Extract for runtime usage
    extract_dir = ensure_dir(version_folder / "bundle")
    # clean existing bundle first
    if extract_dir.exists():
        for child in extract_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    with zipfile.ZipFile(package_path, "r") as zf:
        zf.extractall(extract_dir)
    return extract_dir


def install_plugin(root: Path, plugin: Dict[str, Any], payload: str) -> Path:
    """
    安裝 Plugin 到本機 (PL2)
    1. 解碼 Base64 payload
    2. 解壓縮 package.zip
    3. 讀取 plugin.json 取得 client_entry
    4. 儲存 metadata.json
    """
    ensure_dir(root)
    slug = plugin["slug"]
    folder = ensure_dir(root / slug)
    data = base64.b64decode(payload.encode("ascii"))
    (folder / "package.zip").write_bytes(data)
    
    # 先解壓縮以讀取 plugin.json
    extract_dir = ensure_dir(folder / "bundle")
    if extract_dir.exists():
        for child in extract_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    with zipfile.ZipFile(folder / "package.zip", "r") as zf:
        zf.extractall(extract_dir)
    
    # 從解壓的 plugin.json 讀取 client_entry
    client_entry = "chat_widget.py"  # 默認值
    plugin_json_path = extract_dir / "plugin.json"
    if plugin_json_path.exists():
        try:
            plugin_meta = json.loads(plugin_json_path.read_text(encoding="utf-8"))
            client_entry = plugin_meta.get("client_entry", client_entry)
        except Exception:
            pass
    
    # 保存完整的 metadata，包含 client_entry
    metadata = {
        **plugin,
        "client_entry": client_entry
    }
    (folder / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    
    return extract_dir


class PlayerApp(tk.Tk):
    """
    玩家客戶端主視窗
    
    使用 Tkinter 建構，包含以下分頁:
    - Store: 遊戲商城 (P1)
    - Library: 本機遊戲庫 (P2)
    - Rooms: 房間列表 (P3)
    - Plugins: Plugin 商店 (PL1-PL4)
    
    主要功能:
    - 帳號註冊/登入
    - 瀏覽遊戲、下載遊戲
    - 建立/加入房間、啟動遊戲
    - 評分留言
    - Plugin 購買/安裝/啟用
    """
    def __init__(self) -> None:
        super().__init__()
        self.title("Game Store Lobby Client")
        self.geometry("1080x760")
        
        # 設定視窗關閉處理 (異常關閉時自動登出)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # 網路連線
        self.conn = LobbyConnection()
        self.player: Optional[PlayerInfo] = None
        
        # 遊戲資料
        self.games: List[Dict[str, Any]] = []
        self.game_index: Dict[int, Dict[str, Any]] = {}
        self.local_library: Dict[str, Dict[str, Any]] = {}
        
        # 房間與玩家資料
        self.rooms: List[Dict[str, Any]] = []
        self.active_players: List[Dict[str, Any]] = []
        self.invites: List[Dict[str, Any]] = []
        self.selected_invite_id: Optional[int] = None
        
        # Plugin 系統
        self.plugins: List[Dict[str, Any]] = []
        self.installed_plugins: List[Dict[str, Any]] = []
        self.loaded_plugin_modules: Dict[str, Any] = {}  # 已載入的 Plugin 模組
        self.active_plugin_widgets: Dict[str, Any] = {}  # 活躍的 Plugin Widget
        
        # 遊戲啟動資訊
        self.last_launch_info: Optional[Dict[str, Any]] = None
        self.active_launch_tokens: set[str] = set()
        
        # 自動刷新
        self.auto_refresh_job: Optional[str] = None
        self.auto_refresh_interval_ms = 5000
        self.status_base_text = ""

        # UI 變數
        self.host_var = tk.StringVar(value="127.0.0.1")
        self.port_var = tk.IntVar(value=23002)

        # 建構 UI
        self._build_login()
        self._build_main()
        self.show_login()

    def _build_login(self) -> None:
        """建構登入/註冊頁面 UI"""
        self.login_frame = ttk.Frame(self)
        ttk.Label(self.login_frame, text="Lobby Server Host").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(self.login_frame, textvariable=self.host_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(self.login_frame, text="Port").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(self.login_frame, textvariable=self.port_var).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Label(self.login_frame, text="Username").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self.username_entry = ttk.Entry(self.login_frame)
        self.username_entry.grid(row=2, column=1, sticky="ew", padx=8)
        ttk.Label(self.login_frame, text="Password").grid(row=3, column=0, sticky="w", padx=8, pady=4)
        self.password_entry = ttk.Entry(self.login_frame, show="*")
        self.password_entry.grid(row=3, column=1, sticky="ew", padx=8)
        btn_row = ttk.Frame(self.login_frame)
        btn_row.grid(row=4, column=0, columnspan=2, pady=12)
        ttk.Button(btn_row, text="Register", command=self.register).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="Login", command=self.login).pack(side=tk.LEFT, padx=6)
        self.login_frame.columnconfigure(1, weight=1)



    def _build_main(self) -> None:
        """建構主視窗 UI (登入後顯示)"""
        self.main_frame = ttk.Frame(self)
        
        # 頂部工具列 (包含狀態和 Logout 按鈕)
        toolbar = ttk.Frame(self.main_frame)
        toolbar.pack(fill="x", padx=10, pady=6)
        self.status_label = ttk.Label(toolbar, text="")
        self.status_label.pack(side="left")
        ttk.Button(toolbar, text="Logout", command=self.logout).pack(side="right", padx=5)

        self.tabs = ttk.Notebook(self.main_frame)
        self.tabs.pack(fill="both", expand=True)

        # Store tab
        self.store_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.store_tab, text="Store")
        self.game_tree = ttk.Treeview(self.store_tab, columns=("title", "status", "rating"), show="headings")
        self.game_tree.heading("title", text="Title")
        self.game_tree.heading("status", text="Status")
        self.game_tree.heading("rating", text="Rating")
        self.game_tree.pack(fill="both", expand=True, side=tk.LEFT, padx=10, pady=10)
        self.game_tree.bind("<<TreeviewSelect>>", lambda _evt: self.show_store_details())

        store_controls = ttk.Frame(self.store_tab)
        store_controls.pack(fill="both", expand=False, side=tk.RIGHT, padx=10, pady=10)
        ttk.Button(store_controls, text="Refresh", command=self.refresh_games).pack(fill="x", pady=4)
        ttk.Button(store_controls, text="Download Latest", command=self.download_selected_game).pack(fill="x", pady=4)
        ttk.Button(store_controls, text="Submit Review", command=self.submit_review_dialog).pack(fill="x", pady=4)

        self.store_details = tk.Text(self.store_tab, width=50, state="disabled", wrap="word")
        self.store_details.pack(fill="both", expand=True, side=tk.RIGHT, padx=10, pady=10)

        # Library tab - 本機遊戲庫，顯示已下載的遊戲及更新狀態
        self.library_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.library_tab, text="Library")
        self.library_tree = ttk.Treeview(
            self.library_tab,
            columns=("game", "version", "status", "path"),
            show="headings",
        )
        # 設定欄位標題與寬度
        self.library_tree.heading("game", text="Game")
        self.library_tree.heading("version", text="Version")
        self.library_tree.heading("status", text="Status")
        self.library_tree.heading("path", text="Path")
        self.library_tree.column("game", width=150)
        self.library_tree.column("version", width=80)
        self.library_tree.column("status", width=120)
        self.library_tree.column("path", width=250)
        self.library_tree.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Library 控制按鈕
        lib_btn_frame = ttk.Frame(self.library_tab)
        lib_btn_frame.pack(pady=6)
        ttk.Button(lib_btn_frame, text="Refresh Library", command=self.load_local_library).pack(side="left", padx=4)
        ttk.Button(lib_btn_frame, text="Update Selected", command=self.update_selected_library_game).pack(side="left", padx=4)

        # Rooms tab with paned layout (HW2-style)
        self.rooms_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.rooms_tab, text="Rooms")
        
        rooms_paned = ttk.Panedwindow(self.rooms_tab, orient=tk.HORIZONTAL)
        rooms_paned.pack(fill="both", expand=True, padx=6, pady=6)

        # Left panel: Rooms list + room creation controls
        left_panel = ttk.Frame(rooms_paned, padding=6)
        ttk.Label(left_panel, text="Open Rooms", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        
        self.room_tree = ttk.Treeview(
            left_panel,
            columns=("code", "game", "status", "members"),
            show="headings",
            height=12,
        )
        for col, label in zip(("code", "game", "status", "members"), ("Code", "Game", "Status", "Members")):
            self.room_tree.heading(col, text=label)
            self.room_tree.column(col, width=120)
        self.room_tree.pack(fill="both", expand=True, pady=(4, 6))
        self.room_tree.bind("<<TreeviewSelect>>", lambda _evt: self.show_room_details())

        room_buttons = ttk.Frame(left_panel)
        room_buttons.pack(fill="x", pady=(0, 8))
        ttk.Button(room_buttons, text="Refresh", command=self.refresh_rooms).pack(side=tk.LEFT, padx=2)
        ttk.Button(room_buttons, text="Join", command=self.join_room_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(room_buttons, text="Leave", command=self.leave_room).pack(side=tk.LEFT, padx=2)
        ttk.Button(room_buttons, text="Start Game", command=self.start_room_game).pack(side=tk.LEFT, padx=2)
        self.chat_btn = ttk.Button(room_buttons, text="💬 Chat", command=self.open_room_chat)
        self.chat_btn.pack(side=tk.LEFT, padx=2)

        # Room creation controls
        create_frame = ttk.Labelframe(left_panel, text="Create Room", padding=6)
        create_frame.pack(fill="x", pady=(0, 8))
        
        # Game selection - select from available games list, not entry
        ttk.Label(create_frame, text="Select game from Available Games list").grid(row=0, column=0, columnspan=2, sticky="w", padx=(0, 4))
        
        # Visibility
        ttk.Label(create_frame, text="Visibility:").grid(row=1, column=0, sticky="e", padx=(0, 4), pady=(4, 0))
        self.visibility_var = tk.StringVar(value="public")
        visibility_combo = ttk.Combobox(create_frame, textvariable=self.visibility_var, values=["public", "private"], width=12, state="readonly")
        visibility_combo.grid(row=1, column=1, sticky="w", pady=(4, 0))
        
        ttk.Button(create_frame, text="Create Room", command=self.create_room_dialog).grid(row=2, column=0, columnspan=2, pady=(8, 0))

        # Room details pane
        self.room_details = tk.Text(left_panel, height=6, state="disabled", wrap="word")
        self.room_details.pack(fill="x", pady=(0, 0))

        rooms_paned.add(left_panel, weight=3)

        # Right panel: Online players + Available games
        right_panel = ttk.Frame(rooms_paned, padding=6)
        
        # Online players section
        ttk.Label(right_panel, text="Online Players", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        players_frame = ttk.Frame(right_panel)
        players_frame.pack(fill="both", expand=True, pady=(4, 8))
        self.online_players_list = tk.Listbox(players_frame, height=8, exportselection=False)
        self.online_players_list.pack(side=tk.LEFT, fill="both", expand=True)
        self.online_players_list.bind("<<ListboxSelect>>", self.on_online_player_select)
        players_scroll = ttk.Scrollbar(players_frame, orient=tk.VERTICAL, command=self.online_players_list.yview)
        players_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.online_players_list.configure(yscrollcommand=players_scroll.set)

        players_controls = ttk.Frame(right_panel)
        players_controls.pack(fill="x", pady=(0, 8))
        ttk.Button(players_controls, text="Refresh Players", command=lambda: self.refresh_active_players(silent=False)).pack(side=tk.LEFT, padx=2)

        # Available games section
        ttk.Label(right_panel, text="Available Games", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        games_frame = ttk.Frame(right_panel)
        games_frame.pack(fill="both", expand=True, pady=(4, 8))
        self.available_games_list = tk.Listbox(games_frame, height=6, exportselection=False)
        self.available_games_list.pack(side=tk.LEFT, fill="both", expand=True)
        games_scroll = ttk.Scrollbar(games_frame, orient=tk.VERTICAL, command=self.available_games_list.yview)
        games_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.available_games_list.configure(yscrollcommand=games_scroll.set)

        # Invites section
        ttk.Label(right_panel, text="Invites", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        invites_frame = ttk.Frame(right_panel)
        invites_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.invites_list = tk.Listbox(invites_frame, height=4, exportselection=False)
        self.invites_list.pack(side=tk.LEFT, fill="both", expand=True)
        self.invites_list.bind("<<ListboxSelect>>", self.on_invite_select)
        invites_scroll = ttk.Scrollbar(invites_frame, orient=tk.VERTICAL, command=self.invites_list.yview)
        invites_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.invites_list.configure(yscrollcommand=invites_scroll.set)

        invite_controls = ttk.Frame(right_panel)
        invite_controls.pack(fill="x", pady=(4, 0))
        ttk.Button(invite_controls, text="Refresh", command=self.refresh_invites).pack(side=tk.LEFT, padx=2)
        ttk.Button(invite_controls, text="Accept", command=self.accept_invite).pack(side=tk.LEFT, padx=2)
        ttk.Label(invite_controls, text="Invite:").pack(side=tk.LEFT, padx=(8, 2))
        self.invite_player_entry = ttk.Entry(invite_controls, width=8)
        self.invite_player_entry.pack(side=tk.LEFT)
        ttk.Button(invite_controls, text="Send", command=self.invite_player).pack(side=tk.LEFT, padx=2)

        rooms_paned.add(right_panel, weight=2)

        # Plugins tab - 完整的 Plugin 管理介面
        self.plugins_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.plugins_tab, text="🔌 Plugins")
        
        # Plugin 主區域使用 PanedWindow
        plugin_paned = ttk.PanedWindow(self.plugins_tab, orient="horizontal")
        plugin_paned.pack(fill="both", expand=True, padx=5, pady=5)
        
        # 左側: Plugin 列表
        plugin_list_frame = ttk.LabelFrame(self.plugins_tab, text="Available Plugins")
        plugin_paned.add(plugin_list_frame, weight=1)
        
        # 刷新按鈕
        ttk.Button(plugin_list_frame, text="🔄 Refresh", command=self.refresh_plugins).pack(pady=5, padx=5, fill="x")
        
        # Plugin 樹狀列表
        self.plugin_tree = ttk.Treeview(
            plugin_list_frame,
            columns=("name", "version", "status"),
            show="headings",
            selectmode="browse"
        )
        self.plugin_tree.heading("name", text="Name")
        self.plugin_tree.heading("version", text="Version")
        self.plugin_tree.heading("status", text="Status")
        self.plugin_tree.column("name", width=150)
        self.plugin_tree.column("version", width=80)
        self.plugin_tree.column("status", width=100)
        self.plugin_tree.pack(fill="both", expand=True, padx=5, pady=5)
        self.plugin_tree.bind("<<TreeviewSelect>>", lambda e: self.show_plugin_details())
        
        # 安裝/移除按鈕
        plugin_btn_frame = ttk.Frame(plugin_list_frame)
        plugin_btn_frame.pack(fill="x", padx=5, pady=5)
        ttk.Button(plugin_btn_frame, text="📥 Install", command=self.install_plugin_action).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(plugin_btn_frame, text="🗑️ Remove", command=self.remove_plugin_action).pack(side="left", expand=True, fill="x", padx=2)
        
        # 右側: Plugin 詳細資訊
        plugin_detail_frame = ttk.LabelFrame(self.plugins_tab, text="Plugin Details")
        plugin_paned.add(plugin_detail_frame, weight=1)
        
        self.plugin_details = tk.Text(plugin_detail_frame, wrap="word", state="disabled", height=20)
        self.plugin_details.pack(fill="both", expand=True, padx=5, pady=5)
        
        # 底部提示訊息
        plugin_hint = ttk.Label(
            self.plugins_tab, 
            text="💡 Plugins are optional extensions. Games work normally without any plugins installed.",
            foreground="gray"
        )
        plugin_hint.pack(pady=5)

    def show_login(self) -> None:
        self.main_frame.pack_forget()
        self.login_frame.pack(fill="both", expand=True, padx=20, pady=20)

    def show_main(self) -> None:
        self.login_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)

    def on_close(self) -> None:
        """視窗關閉事件處理 - 自動執行登出清理"""
        self._cleanup_and_logout(silent=True)
        self.destroy()

    def _cleanup_and_logout(self, silent: bool = False) -> None:
        """清理資源並登出 (供 logout 和 on_close 共用)"""
        # 停止自動刷新
        if self.auto_refresh_job:
            self.after_cancel(self.auto_refresh_job)
            self.auto_refresh_job = None
        
        # 通知伺服器登出 (會自動清理房間)
        try:
            if self.player:  # 只有已登入才需要登出
                self.conn.call({"type": "LOGOUT"})
        except Exception:
            if not silent:
                pass  # 忽略錯誤，可能已斷線
        
        # 關閉連線
        self.conn.close()
        
        # 清除狀態
        self.player = None
        self.games = []
        self.rooms = []

    def logout(self) -> None:
        """登出並返回登入畫面"""
        if messagebox.askyesno("Logout", "Are you sure you want to logout?"):
            self._cleanup_and_logout(silent=False)
            self.show_login()

    def ensure_connection(self) -> bool:
        host = self.host_var.get().strip()
        port = int(self.port_var.get())
        try:
            self.conn.connect(host, port)
            return True
        except Exception as exc:
            messagebox.showerror("Connection", str(exc))
            return False

    def register(self) -> None:
        if not self.ensure_connection():
            return
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        try:
            resp = self.conn.call(
                {
                    "type": "REGISTER",
                    "username": username,
                    "passwordHash": hash_password(password),
                    "displayName": username,
                }
            )
        except Exception as exc:
            messagebox.showerror("Register", str(exc))
            return
        if resp.get("ok"):
            messagebox.showinfo("Register", "Registration successful, please login")
        else:
            messagebox.showwarning("Register", resp.get("error", "failed"))

    def login(self) -> None:
        if not self.ensure_connection():
            return
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        try:
            resp = self.conn.call(
                {
                    "type": "LOGIN",
                    "username": username,
                    "passwordHash": hash_password(password),
                }
            )
        except Exception as exc:
            messagebox.showerror("Login", str(exc))
            return
        if not resp.get("ok"):
            messagebox.showwarning("Login", resp.get("error", "failed"))
            return
        player = resp["player"]
        self.player = PlayerInfo(player["id"], player["username"])
        self.active_launch_tokens.clear()
        self.last_launch_info = None
        self.status_base_text = f"Logged in as {self.player.username}"
        self.status_label.config(text=self.status_base_text)
        DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        ensure_dir(DOWNLOAD_ROOT / self.player.username)
        ensure_dir(PLUGIN_ROOT / self.player.username)
        self.show_main()
        self.refresh_games()
        self.load_local_library()
        self.refresh_rooms()
        self.refresh_active_players()
        self.refresh_plugins()
        self.schedule_auto_refresh()

    def refresh_games(self, silent: bool = False) -> None:
        """
        Refresh the game store list.
        Implements Use Case P1: 玩家瀏覽遊戲商城與詳細資訊
        """
        try:
            resp = self.conn.call({"type": "LIST_GAMES"})
        except Exception as exc:
            if not silent:
                # Offer retry option on connection failure
                if messagebox.askretrycancel("Store - Connection Error", 
                    f"Failed to load game list:\n{exc}\n\n"
                    "Please check your connection and try again."):
                    self.refresh_games(silent=False)
            return
        if not resp.get("ok"):
            if not silent:
                error_msg = resp.get("error", "Unknown error")
                if messagebox.askretrycancel("Store - Load Error",
                    f"Failed to load game list:\n{error_msg}\n\n"
                    "Would you like to try again?"):
                    self.refresh_games(silent=False)
            return
        
        self.games = resp.get("games", [])
        self.game_index = {game["id"]: game for game in self.games if "id" in game}
        self.game_tree.delete(*self.game_tree.get_children())
        
        # Filter to only show published games
        published_games = [g for g in self.games if g.get("status", "").lower() == "published"]
        
        if not published_games:
            # Show message when no games are available
            self.store_details.configure(state="normal")
            self.store_details.delete("1.0", tk.END)
            self.store_details.insert(tk.END, 
                "🎮 No Games Available\n\n"
                "There are currently no games available in the store.\n\n"
                "Please check back later or contact the developers\n"
                "to upload some games!")
            self.store_details.configure(state="disabled")
        else:
            for game in published_games:
                name = game.get("title") or "Untitled Game"
                rating = float(game.get("avg_rating", 0)) if game.get("avg_rating") is not None else 0
                reviews = game.get("review_count", 0)
                rating_str = f"★ {rating:.1f} ({reviews})" if reviews > 0 else "No ratings"
                status = (game.get("status") or "?").capitalize()
                self.game_tree.insert("", tk.END, iid=str(game["id"]), values=(name, status, rating_str))
            self.show_store_details()
        
        self.update_available_games_list()

    def get_selected_game(self) -> Optional[Dict[str, Any]]:
        selection = self.game_tree.selection()
        if not selection:
            return None
        gid = int(selection[0])
        for game in self.games:
            if game["id"] == gid:
                return game
        return None

    def format_game_details(self, detail: Dict[str, Any]) -> str:
        """
        Format game details for display.
        Implements Use Case P1: Shows game info including author, description, player count, ratings.
        """
        game = detail.get("game") or {}
        developer = detail.get("developer") or {}
        versions = detail.get("versions") or []
        reviews = detail.get("reviews") or []

        # Game title
        title = game.get("title") or "Untitled Game"
        
        # Developer/Author info
        author_name = developer.get("display_name") or developer.get("username")
        if not author_name:
            author_name = "Unknown Developer"
        
        # Category
        category = game.get("category") or "General"
        
        # Status
        status = (game.get("status") or "unknown").capitalize()
        
        # Player count
        min_players = game.get("min_players") or game.get("minPlayers")
        max_players = game.get("max_players") or game.get("maxPlayers")

        players_line = None
        if min_players and max_players:
            if min_players == max_players:
                players_line = f"{min_players} players"
            else:
                players_line = f"{min_players}-{max_players} players"
        elif min_players:
            players_line = f"{min_players}+ players"
        elif max_players:
            players_line = f"Up to {max_players} players"
        else:
            players_line = "Player count not specified"

        # Supported modes
        support_cli = bool(game.get("support_cli", game.get("supportCli", 0)))
        support_gui = bool(game.get("support_gui", game.get("supportGui", 0)))
        modes = []
        if support_cli:
            modes.append("CLI")
        if support_gui:
            modes.append("GUI")
        mode_str = ", ".join(modes) if modes else "Not specified"

        # Build display text
        lines = []
        lines.append(f"📦 {title}")
        lines.append(f"👤 Developer: {author_name}")
        lines.append(f"🏷️ Category: {category}")
        lines.append(f"📊 Status: {status}")
        lines.append(f"👥 Players: {players_line}")
        lines.append(f"🖥️ Modes: {mode_str}")
        
        # Calculate average rating
        if reviews:
            avg_rating = sum(r.get("rating", 0) for r in reviews) / len(reviews)
            lines.append(f"⭐ Rating: {avg_rating:.1f}/5 ({len(reviews)} reviews)")
        else:
            lines.append(f"⭐ Rating: No ratings yet")

        # Show local vs server version comparison
        game_id = game.get("id")
        # 從版本列表中取得最新版本
        latest_version = max(versions, key=lambda v: v.get("id", 0)) if versions else None
        local_info = self.get_local_version_for_game(game_id) if game_id else None
        
        lines.append("")
        lines.append("─" * 40)
        lines.append("📥 Installation Status:")
        if local_info:
            local_version = local_info.get("version", {})
            local_label = local_version.get("version_label", "unknown")
            downloaded_at = local_info.get("downloaded_at", "unknown")
            if downloaded_at and downloaded_at != "unknown":
                try:
                    dt = datetime.fromisoformat(downloaded_at)
                    downloaded_at = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    pass
            lines.append(f"  ✅ Installed: v{local_label}")
            lines.append(f"     Downloaded: {downloaded_at}")
            
            if latest_version:
                server_label = latest_version.get("version_label", "unknown")
                server_id = latest_version.get("id", 0)
                local_id = local_version.get("id", 0)
                if server_id > local_id:
                    lines.append(f"  🆕 UPDATE AVAILABLE: v{server_label}")
                    lines.append(f"     Click Download to update!")
                else:
                    lines.append(f"  ✓ You have the latest version")
        else:
            lines.append(f"  📭 Not installed")
            if latest_version:
                server_label = latest_version.get("version_label", "unknown")
                lines.append(f"     Latest: v{server_label}")
            lines.append(f"     Click Download to install!")

        # Game description/summary
        lines.append("")
        lines.append("─" * 40)
        lines.append("📝 Description:")
        summary = (game.get("summary") or "").strip()
        if summary:
            lines.append(summary)
        else:
            lines.append("(No description provided)")

        # Available versions
        lines.append("")
        lines.append("─" * 40)
        lines.append("📁 Available Versions:")
        if versions:
            for version in versions[:5]:
                v_label = version.get("version_label") or version.get("versionLabel") or "unnamed"
                client_mode = (version.get("client_mode") or version.get("clientMode") or "gui").upper()
                changelog = (version.get("changelog") or "").strip()
                lines.append(f"  • v{v_label} [{client_mode}]")
                if changelog:
                    # Show first line of changelog
                    first_line = changelog.splitlines()[0].strip()
                    if first_line:
                        lines.append(f"      {first_line[:60]}{'...' if len(first_line) > 60 else ''}")
            if len(versions) > 5:
                lines.append(f"  ... and {len(versions) - 5} more version(s)")
        else:
            lines.append("  (No versions available)")

        # Reviews section
        lines.append("")
        lines.append("─" * 40)
        lines.append("💬 Recent Reviews:")
        if reviews:
            for review in reviews[:5]:
                reviewer = review.get("player_name") or "Anonymous"
                rating = int(review.get("rating", 0))  # 轉換為整數
                comment = (review.get("comment") or "").strip()
                stars = "★" * rating + "☆" * (5 - rating)
                lines.append(f"  {stars} by @{reviewer}")
                if comment:
                    # Show first line of comment
                    first_line = comment.splitlines()[0].strip()
                    if first_line:
                        lines.append(f"      \"{first_line[:50]}{'...' if len(first_line) > 50 else ''}\"")
            if len(reviews) > 5:
                lines.append(f"  ... and {len(reviews) - 5} more review(s)")
        else:
            lines.append("  (No reviews yet - be the first to review!)")

        return "\n".join(lines)

    def show_store_details(self) -> None:
        """Show detailed information for the selected game."""
        game = self.get_selected_game()
        self.store_details.configure(state="normal")
        self.store_details.delete("1.0", tk.END)
        if not game:
            self.store_details.insert(tk.END, 
                "👈 Select a game from the list to view details\n\n"
                "You can:\n"
                "• Click on a game to see its information\n"
                "• Download games to your library\n"
                "• Submit reviews for games you've played")
        else:
            try:
                detail = self.conn.call({"type": "GET_GAME_DETAILS", "gameId": game["id"]})
                if detail.get("ok"):
                    formatted = self.format_game_details(detail)
                    self.store_details.insert(tk.END, formatted)
                else:
                    error = detail.get("error", "Unknown error")
                    self.store_details.insert(tk.END, 
                        f"❌ Error loading details\n\n"
                        f"{error}\n\n"
                        "Please try selecting the game again or refresh the list.")
            except Exception as exc:
                import traceback
                self.store_details.insert(tk.END, 
                    f"❌ Failed to load game details\n\n"
                    f"Error: {exc}\n\n"
                    f"Debug: {traceback.format_exc()}\n\n"
                    "Please check your connection and try refreshing.")
        self.store_details.configure(state="disabled")

    def get_local_version_for_game(self, game_id: int) -> Optional[Dict[str, Any]]:
        """Get the locally installed version information for a game."""
        if not self.player:
            return None
        root = DOWNLOAD_ROOT / self.player.username
        if not root.exists():
            return None
        
        # Find game folder by game_id prefix
        for game_folder in root.iterdir():
            if not game_folder.is_dir():
                continue
            folder_name = game_folder.name
            # Game folders are named "{game_id}_{title}"
            if folder_name.startswith(f"{game_id}_"):
                versions_dir = game_folder / "versions"
                if not versions_dir.exists():
                    continue
                # Find latest installed version
                latest_version = None
                latest_time = None
                for version_dir in versions_dir.iterdir():
                    meta_path = version_dir / "metadata.json"
                    if meta_path.exists():
                        try:
                            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                            downloaded_at = metadata.get("downloaded_at")
                            if latest_time is None or (downloaded_at and downloaded_at > latest_time):
                                latest_time = downloaded_at
                                latest_version = metadata
                        except (json.JSONDecodeError, KeyError):
                            continue
                return latest_version
        return None

    def download_selected_game(self) -> None:
        game = self.get_selected_game()
        if not game:
            messagebox.showwarning("Download", "Select a game first")
            return
        
        # First, get game details to know the latest version
        try:
            detail = self.conn.call({"type": "GET_GAME_DETAILS", "gameId": game["id"]})
        except Exception as exc:
            messagebox.showerror("Download", f"Failed to get game details: {exc}")
            return
        
        if not detail.get("ok"):
            messagebox.showerror("Download", detail.get("error", "Failed to get game details"))
            return
        
        # 從版本列表中取得最新版本
        versions = detail.get("versions", [])
        if versions:
            # 按 id 排序取最新的
            server_version = max(versions, key=lambda v: v.get("id", 0))
            server_version_label = server_version.get("version_label", "unknown")
        else:
            server_version = None
            server_version_label = "No version available"
        
        if not server_version:
            messagebox.showwarning("Download", "No downloadable version available for this game")
            return
        
        # Check if we already have this game installed
        local_info = self.get_local_version_for_game(game["id"])
        if local_info:
            local_version = local_info.get("version", {})
            local_version_label = local_version.get("version_label", "unknown")
            local_version_id = local_version.get("id", 0)
            server_version_id = server_version.get("id", 0) if server_version else 0
            
            # Compare versions
            if server_version_id <= local_version_id:
                # Already have the latest or newer
                result = messagebox.askquestion(
                    "Already Installed",
                    f"You already have version {local_version_label} installed.\n\n"
                    f"Server version: {server_version_label}\n\n"
                    "Do you want to re-download and reinstall?",
                    icon="question"
                )
                if result != "yes":
                    return
            else:
                # Newer version available
                result = messagebox.askquestion(
                    "Update Available",
                    f"📦 A newer version is available!\n\n"
                    f"Your version: {local_version_label}\n"
                    f"Latest version: {server_version_label}\n\n"
                    "Do you want to update to the latest version?",
                    icon="info"
                )
                if result != "yes":
                    return
        
        # Proceed with download - show progress window
        progress_win = tk.Toplevel(self)
        progress_win.title("Downloading...")
        progress_win.geometry("300x100")
        progress_win.transient(self)
        progress_win.grab_set()
        
        ttk.Label(progress_win, text=f"Downloading {game['title']}...").pack(pady=10)
        progress_label = ttk.Label(progress_win, text="Connecting to server...")
        progress_label.pack(pady=5)
        progress_win.update()
        
        max_retries = 3
        resp = None
        last_error = None
        
        for attempt in range(max_retries):
            try:
                progress_label.config(text=f"Downloading... (attempt {attempt + 1}/{max_retries})")
                progress_win.update()
                resp = self.conn.call({"type": "DOWNLOAD_GAME", "gameId": game["id"]})
                if resp.get("ok"):
                    break
                last_error = resp.get("error", "Unknown error")
            except Exception as exc:
                last_error = str(exc)
                if attempt < max_retries - 1:
                    progress_label.config(text=f"Retrying... ({attempt + 2}/{max_retries})")
                    progress_win.update()
                    time.sleep(1)
        
        progress_win.destroy()
        
        if not resp or not resp.get("ok"):
            result = messagebox.askretrycancel(
                "Download Failed", 
                f"Failed to download game.\n\nError: {last_error}\n\nDo you want to retry?"
            )
            if result:
                self.download_selected_game()  # Retry
            return
        
        target_root = DOWNLOAD_ROOT / self.player.username
        try:
            # Pass SHA256 hash for integrity verification if provided
            expected_sha256 = resp.get("sha256")
            extract_dir = save_package(
                target_root, 
                resp["game"], 
                resp["version"], 
                resp["package"],
                expected_sha256=expected_sha256
            )
        except ValueError as ve:
            # Integrity check failed
            messagebox.showerror(
                "Download Failed", 
                f"Package integrity verification failed:\n\n{ve}\n\n"
                "The download may have been corrupted. Please try again."
            )
            return
        except Exception as exc:
            messagebox.showerror("Download", f"Failed to save package: {exc}")
            return
        
        messagebox.showinfo(
            "Download Complete", 
            f"✅ Successfully installed!\n\n"
            f"Game: {resp['game']['title']}\n"
            f"Version: {resp['version']['version_label']}\n\n"
            f"You can find it in your Library tab."
        )
        self.load_local_library()

    def load_local_library(self) -> None:
        """
        載入本機遊戲庫並檢查更新狀態 (P2)
        會比對伺服器上的最新版本，顯示是否有更新可用
        """
        self.library_tree.delete(*self.library_tree.get_children())
        self.local_library.clear()
        if not self.player:
            return
        root = DOWNLOAD_ROOT / self.player.username
        if not root.exists():
            return
        
        # 建立 game_id -> 伺服器最新版本的對照表
        server_latest_versions: Dict[int, Dict[str, Any]] = {}
        for game in self.games:
            game_id = game.get("id")
            if game_id:
                server_latest_versions[game_id] = {
                    "latest_version_id": game.get("latest_version_id"),
                    "latest_version_label": game.get("latest_version_label", "?"),
                    "status": game.get("status", "unknown"),
                }
        
        for game_folder in root.iterdir():
            if not game_folder.is_dir():
                continue
            versions_dir = game_folder / "versions"
            if not versions_dir.exists():
                continue
            for version_dir in versions_dir.iterdir():
                if not version_dir.is_dir():
                    continue
                meta_path = version_dir / "metadata.json"
                if not meta_path.exists():
                    continue
                try:
                    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                game_id = metadata['game']['id']
                local_version_id = metadata['version']['id']
                # 使用資料夾路徑作為 key，避免重複 game_id 造成衝突
                key = str(version_dir)
                
                # 如果這個 game_id::version_id 已經存在，跳過重複項目
                lib_key = f"{game_id}::{local_version_id}"
                if lib_key in self.local_library:
                    continue
                    
                self.local_library[lib_key] = {
                    "game": metadata["game"],
                    "version": metadata["version"],
                    "bundle": version_dir / "bundle",
                    "tree_key": key,  # 儲存 tree key 以供後續使用
                }
                
                # 判斷更新狀態
                server_info = server_latest_versions.get(game_id)
                if server_info:
                    server_latest_id = server_info.get("latest_version_id")
                    server_status = server_info.get("status", "").lower()
                    server_latest_label = server_info.get("latest_version_label", "?")
                    
                    if server_status == "retired":
                        status_text = "🚫 Retired"
                    elif server_status == "unpublished":
                        status_text = "⚠️ Unpublished"
                    elif server_latest_id and server_latest_id > local_version_id:
                        status_text = f"🔄 Update → {server_latest_label}"
                    else:
                        status_text = "✅ Up to date"
                else:
                    # 伺服器上找不到此遊戲
                    # 如果已經從伺服器取得遊戲列表但找不到，表示遊戲已被刪除
                    if self.games:  # 已取得伺服器遊戲列表
                        status_text = "🗑️ Deleted"
                    else:
                        status_text = "❓ Unknown"
                
                self.library_tree.insert(
                    "",
                    tk.END,
                    iid=key,
                    values=(
                        metadata["game"]["title"], 
                        metadata["version"]["version_label"], 
                        status_text,
                        str(version_dir / "bundle")
                    ),
                )

    def update_selected_library_game(self) -> None:
        """
        更新 Library 中選中的遊戲到最新版本
        會自動下載伺服器上的最新版本
        """
        selection = self.library_tree.selection()
        if not selection:
            messagebox.showinfo("Update", "Please select a game to update.")
            return
        
        tree_key = selection[0]
        # 從 local_library 中找到對應的項目 (透過 tree_key)
        item = None
        for lib_key, lib_item in self.local_library.items():
            if lib_item.get("tree_key") == tree_key:
                item = lib_item
                break
        if not item:
            return
        
        game_id = item["game"]["id"]
        local_version_id = item["version"]["id"]
        
        # 從伺服器遊戲列表找到對應遊戲
        server_game = self.game_index.get(game_id)
        if not server_game:
            messagebox.showwarning("Update", "Game not found on server. It may have been removed.")
            return
        
        server_latest_id = server_game.get("latest_version_id")
        server_status = server_game.get("status", "").lower()
        
        if server_status == "retired":
            messagebox.showinfo("Update", "This game has been retired and is no longer available.")
            return
        
        if server_status == "unpublished":
            messagebox.showinfo("Update", "This game is currently unpublished.")
            return
        
        if not server_latest_id or server_latest_id <= local_version_id:
            messagebox.showinfo("Update", "Your game is already up to date!")
            return
        
        # 執行下載更新
        if messagebox.askyesno("Update Available", 
            f"Update {server_game['title']} from v{item['version']['version_label']} "
            f"to v{server_game.get('latest_version_label', '?')}?"):
            self._download_game_by_id(game_id)

    def _download_game_by_id(self, game_id: int) -> None:
        """
        根據 game_id 下載最新版本 (供 update_selected_library_game 使用)
        """
        game = self.game_index.get(game_id)
        if not game:
            messagebox.showerror("Download", "Game not found.")
            return
        
        # 顯示進度視窗
        progress_win = tk.Toplevel(self)
        progress_win.title("Downloading...")
        progress_win.geometry("300x100")
        progress_win.transient(self)
        progress_win.grab_set()
        
        ttk.Label(progress_win, text=f"Downloading {game['title']}...").pack(pady=10)
        progress_label = ttk.Label(progress_win, text="Connecting to server...")
        progress_label.pack(pady=5)
        progress_win.update()
        
        max_retries = 3
        resp = None
        last_error = None
        
        for attempt in range(max_retries):
            try:
                progress_label.config(text=f"Downloading... (attempt {attempt + 1}/{max_retries})")
                progress_win.update()
                resp = self.conn.call({"type": "DOWNLOAD_GAME", "gameId": game_id})
                if resp.get("ok"):
                    break
                last_error = resp.get("error", "Unknown error")
            except Exception as exc:
                last_error = str(exc)
                if attempt < max_retries - 1:
                    progress_label.config(text=f"Retrying... ({attempt + 2}/{max_retries})")
                    progress_win.update()
                    time.sleep(1)
        
        progress_win.destroy()
        
        if not resp or not resp.get("ok"):
            messagebox.showerror("Download Failed", f"Failed to download game.\n\nError: {last_error}")
            return
        
        target_root = DOWNLOAD_ROOT / self.player.username
        try:
            expected_sha256 = resp.get("sha256")
            save_package(
                target_root, 
                resp["game"], 
                resp["version"], 
                resp["package"],
                expected_sha256=expected_sha256
            )
        except Exception as exc:
            messagebox.showerror("Download", f"Failed to save package: {exc}")
            return
        
        messagebox.showinfo(
            "Update Complete", 
            f"✅ Successfully updated!\n\n"
            f"Game: {resp['game']['title']}\n"
            f"Version: {resp['version']['version_label']}"
        )
        # 重新整理 Library 和 Store
        self.refresh_games()
        self.load_local_library()

    def get_selected_library_item(self) -> Optional[Dict[str, Any]]:
        selection = self.library_tree.selection()
        if not selection:
            return None
        tree_key = selection[0]
        # 從 local_library 中找到對應的項目 (透過 tree_key)
        for lib_key, lib_item in self.local_library.items():
            if lib_item.get("tree_key") == tree_key:
                return lib_item
        return None

    def launch_selected_game(self) -> None:
        if not self.player:
            return
        if not self.last_launch_info:
            messagebox.showwarning("Launch", "No active game launch info. Start or join a room first.")
            return
        self.launch_game_payload(self.last_launch_info, force=True)

    def refresh_rooms(self, silent: bool = False) -> None:
        """刷新房間列表，保留當前選擇狀態"""
        try:
            resp = self.conn.call({"type": "LIST_ROOMS"})
        except Exception as exc:
            if not silent:
                messagebox.showerror("Rooms", str(exc))
            return
        if not resp.get("ok"):
            if not silent:
                messagebox.showwarning("Rooms", resp.get("error", "failed"))
            return
        # 保存當前選擇的房間 ID
        selected_items = self.room_tree.selection()
        selected_room_id = selected_items[0] if selected_items else None
        
        self.rooms = resp.get("rooms", [])
        self.room_tree.delete(*self.room_tree.get_children())
        for item in self.rooms:
            room = item["room"]
            members = item.get("members", [])
            owner_id = room.get("owner_player_id")
            # 在成員列表中標示 Host
            member_display = []
            for idx, m in enumerate(members, start=1):
                username = m.get("username") or f"Player {idx}"
                if m.get("player_id") == owner_id:
                    member_display.append(f"👑{username}")
                else:
                    member_display.append(username)
            member_names = ", ".join(member_display) or "Waiting"
            game_meta = self.game_index.get(room.get("game_id"), {})
            game_title = game_meta.get("title") or f"Game #{room.get('game_id')}"
            capacity = room.get("capacity") or len(members) or 0
            occupancy = f"{len(members)}/{capacity}" if capacity else str(len(members))
            status = (room.get("status") or "waiting").capitalize()
            version_id = room.get("game_version_id")
            game_label = f"{game_title} · v#{version_id}" if version_id else game_title
            status_with_count = f"{status} ({occupancy})"
            self.room_tree.insert(
                "",
                tk.END,
                iid=str(room["id"]),
                values=(room["code"], game_label, status_with_count, member_names),
            )
            self.ensure_room_auto_launch(room, members)
        
        # 恢復之前選擇的房間（如果該房間仍然存在）
        if selected_room_id and self.room_tree.exists(selected_room_id):
            self.room_tree.selection_set(selected_room_id)
            self.room_tree.focus(selected_room_id)
            # 確保選擇的項目可見
            self.room_tree.see(selected_room_id)
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        room_count = len(self.rooms)
        player_count = len(self.active_players) if hasattr(self, 'active_players') else 0
        summary = f"{room_count} room(s) · {player_count} online"
        if self.status_base_text:
            self.status_label.config(text=f"{self.status_base_text} · {summary} (updated {timestamp})")
        else:
            self.status_label.config(text=f"{summary} (updated {timestamp})")
        self.show_room_details()

    def update_status_summary(self) -> None:
        """更新狀態列摘要（房間數和線上人數）"""
        if not hasattr(self, "status_label"):
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        room_count = len(self.rooms) if hasattr(self, 'rooms') else 0
        player_count = len(self.active_players) if hasattr(self, 'active_players') else 0
        summary = f"{room_count} room(s) · {player_count} online"
        if self.status_base_text:
            self.status_label.config(text=f"{self.status_base_text} · {summary} (updated {timestamp})")
        else:
            self.status_label.config(text=f"{summary} (updated {timestamp})")

    def refresh_active_players(self, silent: bool = False) -> None:
        try:
            resp = self.conn.call({"type": "LIST_ACTIVE_PLAYERS"})
        except Exception as exc:
            if not silent:
                messagebox.showerror("Players", str(exc))
            return
        if not resp.get("ok"):
            if not silent:
                messagebox.showwarning("Players", resp.get("error", "failed"))
            return
        self.active_players = resp.get("players", [])
        self.update_online_players_list()
        self.update_status_summary()

    def update_online_players_list(self) -> None:
        """Update the online players listbox in the Rooms tab"""
        if not hasattr(self, "online_players_list"):
            return
        self.online_players_list.delete(0, tk.END)
        for player in self.active_players:
            username = player.get("username") or "--"
            player_id = player.get("id") or player.get("playerId") or "?"
            state = player.get("state", "Idle")
            display = f"@{username} (ID: {player_id}) [{state}]"
            self.online_players_list.insert(tk.END, display)

    def update_available_games_list(self) -> None:
        """Update the available games listbox in the Rooms tab"""
        if not hasattr(self, "available_games_list"):
            return
        self.available_games_list.delete(0, tk.END)
        for game in self.games:
            if game.get("status", "").lower() == "published":
                title = game.get("title") or "Untitled"
                # game_id = game.get("id") or ""
                # display = f"{title} (id={game_id})"
                display = title
                self.available_games_list.insert(tk.END, display)

    def refresh_everything(self) -> None:
        self.refresh_games()
        self.load_local_library()
        self.refresh_rooms()
        self.refresh_active_players()
        self.refresh_invites()

    def refresh_invites(self, silent: bool = True) -> None:
        """Refresh the invites list"""
        try:
            resp = self.conn.call({"type": "LIST_INVITES"})
        except Exception as exc:
            if not silent:
                messagebox.showerror("Invites", str(exc))
            return
        if not resp.get("ok"):
            if not silent:
                messagebox.showwarning("Invites", resp.get("error", "failed"))
            return
        self.invites = resp.get("invites", [])
        self.update_invites_list()

    def update_invites_list(self) -> None:
        """Update the invites listbox"""
        if not hasattr(self, "invites_list"):
            return
        self.invites_list.delete(0, tk.END)
        for invite in self.invites:
            from_user = invite.get("from_username") or f"User {invite.get('from_player_id')}"
            game_title = invite.get("game_title") or "Game"
            room_code = invite.get("room_code") or "???"
            display = f"{from_user} → {game_title} ({room_code})"
            self.invites_list.insert(tk.END, display)

    def on_invite_select(self, event: Any) -> None:
        """Handle invite selection"""
        selection = self.invites_list.curselection()
        if selection and self.invites:
            idx = selection[0]
            if idx < len(self.invites):
                self.selected_invite_id = self.invites[idx].get("id")

    def on_online_player_select(self, event: Any) -> None:
        """Handle online player selection - auto-fill invite entry with player ID"""
        selection = self.online_players_list.curselection()
        if selection and self.active_players:
            idx = selection[0]
            if idx < len(self.active_players):
                player = self.active_players[idx]
                player_id = player.get("id")
                if player_id:
                    self.invite_player_entry.delete(0, tk.END)
                    self.invite_player_entry.insert(0, str(player_id))

    def invite_player(self) -> None:
        """Send an invite to a player"""
        if not self.player:
            return
        room = self.get_selected_room()
        if not room:
            messagebox.showwarning("Invite", "Select a room first")
            return
        if room.get("owner_player_id") != self.player.id:
            messagebox.showwarning("Invite", "Only room host can send invites")
            return
        
        player_id_str = self.invite_player_entry.get().strip()
        if not player_id_str:
            messagebox.showwarning("Invite", "Enter player ID")
            return
        
        try:
            to_player_id = int(player_id_str)
        except ValueError:
            messagebox.showwarning("Invite", "Invalid player ID")
            return
        
        try:
            resp = self.conn.call({
                "type": "INVITE",
                "roomId": room["id"],
                "toPlayerId": to_player_id,
            })
        except Exception as exc:
            messagebox.showerror("Invite", str(exc))
            return
        
        if resp.get("ok"):
            messagebox.showinfo("Invite", "Invite sent successfully")
            self.invite_player_entry.delete(0, tk.END)
        else:
            messagebox.showwarning("Invite", resp.get("error", "failed"))

    def accept_invite(self) -> None:
        """Accept the selected invite"""
        if not self.selected_invite_id:
            messagebox.showwarning("Accept Invite", "Select an invite first")
            return
        
        try:
            resp = self.conn.call({
                "type": "ACCEPT_INVITE",
                "inviteId": self.selected_invite_id,
            })
        except Exception as exc:
            messagebox.showerror("Accept Invite", str(exc))
            return
        
        if resp.get("ok"):
            messagebox.showinfo("Accept Invite", "Joined room successfully")
            self.refresh_invites()
            self.refresh_rooms()
        else:
            messagebox.showwarning("Accept Invite", resp.get("error", "failed"))

    def schedule_auto_refresh(self) -> None:
        if self.auto_refresh_job:
            self.after_cancel(self.auto_refresh_job)
        self.auto_refresh_job = self.after(self.auto_refresh_interval_ms, self.poll_updates)

    def poll_updates(self) -> None:
        if not self.player:
            self.auto_refresh_job = None
            return
        self.refresh_rooms(silent=True)
        self.refresh_active_players(silent=True)
        self.refresh_invites(silent=True)
        self.schedule_auto_refresh()









    def show_room_details(self) -> None:
        if not hasattr(self, "room_details"):
            return
        selection = self.room_tree.selection()
        details = self.room_details
        
        # 保存當前滾動位置
        scroll_pos = details.yview()
        
        details.configure(state="normal")
        details.delete("1.0", tk.END)
        if not selection:
            details.insert(tk.END, "👈 Select a room to view details\n\n"
                "You can:\n"
                "• Join an existing room\n"
                "• Create a new room by selecting a game\n"
                "• Invite friends to join")
        else:
            rid = int(selection[0])
            item = next((item for item in self.rooms if item["room"].get("id") == rid), None)
            if not item:
                details.insert(tk.END, "Room info unavailable")
            else:
                room = item["room"]
                members = item.get("members", [])
                game_meta = self.game_index.get(room.get("game_id"), {})
                
                lines = []
                # Room header
                status = (room.get('status') or 'unknown').capitalize()
                status_emoji = {"Waiting": "⏳", "Playing": "🎮", "Launching": "🚀", "Finished": "🏁"}.get(status, "❓")
                lines.append(f"{status_emoji} Room {room.get('code')} · {status}")
                lines.append("")
                
                # Game info
                lines.append(f"🎯 Game: {game_meta.get('title') or 'Unknown Game'}")
                min_players = game_meta.get('min_players', 1)
                max_players = game_meta.get('max_players', room.get('capacity', 4))
                lines.append(f"👥 Players: {min_players}-{max_players}")
                lines.append(f"📊 Current: {len(members)}/{room.get('capacity', len(members))}")
                
                # Check if ready to start
                if len(members) >= min_players:
                    lines.append("✅ Enough players, ready to start")
                else:
                    needed = min_players - len(members)
                    lines.append(f"⚠️ Need {needed} more player(s) to start")
                
                lines.append("")
                lines.append("─" * 30)
                
                # Members list
                if members:
                    lines.append("👤 Room Members:")
                    for member in members:
                        is_owner = member.get("player_id") == room.get("owner_player_id")
                        is_me = self.player and member.get("player_id") == self.player.id
                        
                        prefix = ""
                        if is_owner:
                            prefix = "👑 "  # Crown for owner
                        elif is_me:
                            prefix = "→ "
                        else:
                            prefix = "  "
                        
                        suffix = ""
                        if is_owner and is_me:
                            suffix = " (Host, You)"
                        elif is_owner:
                            suffix = " (Host)"
                        elif is_me:
                            suffix = " (You)"
                        
                        lines.append(f"{prefix}{member.get('username')}{suffix}")
                else:
                    lines.append("📭 No members yet")
                
                # Room owner actions hint
                lines.append("")
                if self.player and room.get("owner_player_id") == self.player.id:
                    lines.append("💡 You are the host:")
                    lines.append("  • Click 'Start Game' to start")
                    lines.append("  • Invite other players to join")
                elif self.player and any(m.get("player_id") == self.player.id for m in members):
                    lines.append("💡 Waiting for host to start...")
                else:
                    lines.append("💡 Click 'Join' to join this room")
                
                # Plugin 提示 (Use Case PL4)
                lines.append("")
                lines.append("─" * 30)
                if self.is_plugin_installed("room_chat"):
                    lines.append("🔌 Room Chat Plugin installed")
                    lines.append("   Chat available after joining room")
                else:
                    lines.append("💡 Tip: Install 'Room Chat Plugin'")
                    lines.append("   to chat with other players in room")
                    lines.append("   (Optional, does not affect gameplay)")
                
                details.insert(tk.END, "\n".join(lines))
        details.configure(state="disabled")
        
        # 恢復滾動位置（只有在有滾動時才恢復）
        if scroll_pos[0] > 0 or scroll_pos[1] < 1:
            details.yview_moveto(scroll_pos[0])

    def check_game_version_requirement(self, game_id: int, required_version_id: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        """
        Check if player has the required game version installed.
        If required_version_id is None, checks for latest version.
        
        Returns:
            (is_ready, error_message)
            - (True, None) if version is installed and up to date
            - (False, message) if action needed
        """
        try:
            # Get game details to find latest version
            detail = self.conn.call({"type": "GET_GAME_DETAILS", "gameId": game_id})
            if not detail.get("ok"):
                return False, f"Failed to get game info: {detail.get('error', 'Unknown error')}"
            
            # 從版本列表中取得最新版本
            versions = detail.get("versions", [])
            if not versions:
                return False, "No available version for this game"
            
            latest_version = max(versions, key=lambda v: v.get("id", 0))
            
            # If specific version required, check that version
            target_version_id = required_version_id or latest_version.get("id")
            target_version_label = latest_version.get("version_label", "unknown")
            
            # Check local installation
            local_info = self.get_local_version_for_game(game_id)
            
            if not local_info:
                return False, f"NEED_DOWNLOAD:{game_id}:{target_version_label}"
            
            local_version = local_info.get("version", {})
            local_version_id = local_version.get("id", 0)
            local_version_label = local_version.get("version_label", "unknown")
            
            if local_version_id < target_version_id:
                return False, f"NEED_UPDATE:{game_id}:{local_version_label}:{target_version_label}"
            
            return True, None
            
        except Exception as exc:
            return False, f"Error checking version: {exc}"

    def prompt_download_or_update(self, game_id: int, error_msg: str) -> bool:
        """
        Show download/update prompt based on error message.
        Returns True if user wants to proceed with download.
        """
        if error_msg.startswith("NEED_DOWNLOAD:"):
            parts = error_msg.split(":")
            version_label = parts[2] if len(parts) > 2 else "latest"
            result = messagebox.askquestion(
                "Download Required",
                f"📭 You haven't downloaded this game!\n\n"
                f"Required version: v{version_label}\n\n"
                f"Download now?",
                icon="warning"
            )
            return result == "yes"
        
        elif error_msg.startswith("NEED_UPDATE:"):
            parts = error_msg.split(":")
            local_ver = parts[2] if len(parts) > 2 else "?"
            server_ver = parts[3] if len(parts) > 3 else "?"
            result = messagebox.askquestion(
                "Update Required",
                f"🆕 Your game version is outdated!\n\n"
                f"Your version: v{local_ver}\n"
                f"Latest version: v{server_ver}\n\n"
                f"You must update to the latest version to play.\n"
                f"Update now?",
                icon="warning"
            )
            return result == "yes"
        
        else:
            messagebox.showerror("Version Check Failed", error_msg)
            return False

    def download_game_for_play(self, game_id: int) -> bool:
        """
        Download the latest version of a game for playing.
        Returns True if download successful.
        """
        try:
            # Show progress
            progress_win = tk.Toplevel(self)
            progress_win.title("Downloading...")
            progress_win.geometry("300x100")
            progress_win.transient(self)
            progress_win.grab_set()
            
            ttk.Label(progress_win, text="Downloading game...").pack(pady=10)
            progress_label = ttk.Label(progress_win, text="Connecting...")
            progress_label.pack(pady=5)
            progress_win.update()
            
            resp = self.conn.call({"type": "DOWNLOAD_GAME", "gameId": game_id})
            
            progress_win.destroy()
            
            if not resp.get("ok"):
                messagebox.showerror("Download Failed", resp.get("error", "Download failed"))
                return False
            
            target_root = DOWNLOAD_ROOT / self.player.username
            expected_sha256 = resp.get("sha256")
            
            try:
                save_package(
                    target_root,
                    resp["game"],
                    resp["version"],
                    resp["package"],
                    expected_sha256=expected_sha256
                )
            except Exception as exc:
                messagebox.showerror("Save Failed", f"Failed to save game files: {exc}")
                return False
            
            self.load_local_library()
            messagebox.showinfo(
                "Download Complete",
                f"✅ Successfully installed!\n\n"
                f"Game: {resp['game']['title']}\n"
                f"Version: v{resp['version']['version_label']}"
            )
            return True
            
        except Exception as exc:
            messagebox.showerror("Download Error", str(exc))
            return False

    def create_room_dialog(self) -> None:
        if not self.player:
            return
        
        # Get game from selected game in available games list
        selection = self.available_games_list.curselection()
        if not selection or not self.games:
            messagebox.showwarning("Create Room", "Please select a game from the Available Games list")
            return
        
        idx = selection[0]
        published_games = [g for g in self.games if g.get("status", "").lower() == "published"]
        if idx >= len(published_games):
            messagebox.showwarning("Create Room", "Selected game not found")
            return
        
        game = published_games[idx]
        game_id = game["id"]
        
        # Check if player has latest version before creating room
        is_ready, error_msg = self.check_game_version_requirement(game_id)
        if not is_ready:
            if self.prompt_download_or_update(game_id, error_msg):
                if not self.download_game_for_play(game_id):
                    return  # Download failed
                # Re-check after download
                is_ready, error_msg = self.check_game_version_requirement(game_id)
                if not is_ready:
                    messagebox.showerror("Version Check", "Version still doesn't match after download")
                    return
            else:
                return  # User declined download
        
        default_capacity = game.get("max_players") or 4
        visibility = self.visibility_var.get()
        
        try:
            resp = self.conn.call(
                {
                    "type": "CREATE_ROOM",
                    "gameId": game_id,
                    "capacity": default_capacity,
                    "visibility": visibility,
                }
            )
        except Exception as exc:
            messagebox.showerror("Create Room", str(exc))
            return
        if resp.get("ok"):
            room_code = resp.get('roomCode', '???')
            room_id = resp.get('roomId', '?')
            messagebox.showinfo(
                "Room Created",
                f"✅ Room created successfully!\n\n"
                f"🎮 Game: {game.get('title', 'Unknown')}\n"
                f"🏠 Room Code: {room_code}\n"
                f"👥 Capacity: {default_capacity}\n"
                f"🔒 Visibility: {visibility}\n\n"
                f"Share this code with friends to join!",
            )
            self.refresh_rooms()
        else:
            error = resp.get("error", "failed")
            # Provide more helpful error messages
            if "retired" in error.lower() or "下架" in error:
                messagebox.showwarning("Cannot Create Room", f"This game has been retired.\n\n{error}")
            elif "not published" in error.lower() or "draft" in error.lower():
                messagebox.showwarning("Cannot Create Room", f"This game is not published yet.\n\n{error}")
            else:
                messagebox.showwarning("Create Room", error)

    def join_room_dialog(self) -> None:
        room = self.get_selected_room()
        if not room:
            messagebox.showwarning("Join Room", "Please select a room from the list")
            return

        room_id = room["id"]
        game_id = room.get("game_id")
        game_version_id = room.get("game_version_id")
        
        members: List[Dict[str, Any]] = []
        for item in self.rooms:
            if item["room"]["id"] == room_id:
                members = item.get("members", [])
                break
        if self.is_player_in_members(members):
            messagebox.showinfo("Join Room", "You are already in this room")
            return
        
        # Check if player has the required game version before joining
        if game_id:
            is_ready, error_msg = self.check_game_version_requirement(game_id, game_version_id)
            if not is_ready:
                if self.prompt_download_or_update(game_id, error_msg):
                    if not self.download_game_for_play(game_id):
                        return  # Download failed
                    # Re-check after download
                    is_ready, error_msg = self.check_game_version_requirement(game_id, game_version_id)
                    if not is_ready:
                        messagebox.showerror("Version Check", "Version still doesn't match after download")
                        return
                else:
                    return  # User declined download

        try:
            resp = self.conn.call({"type": "JOIN_ROOM", "roomId": room_id})
        except Exception as exc:
            messagebox.showerror("Join Room", str(exc))
            return
        if resp.get("ok"):
            messagebox.showinfo("Join Room", "✅ Successfully joined room!")
            self.refresh_rooms()
        else:
            error = resp.get("error", "failed")
            if "full" in error.lower() or "已滿" in error:
                # Room is full, suggest other options
                other_rooms = [r for r in self.rooms 
                              if r["room"].get("game_id") == game_id 
                              and r["room"].get("id") != room_id
                              and len(r.get("members", [])) < r["room"].get("capacity", 0)]
                if other_rooms:
                    messagebox.showwarning(
                        "Room Full",
                        f"This room has reached its capacity.\n\n"
                        f"💡 Tip: There are {len(other_rooms)} other room(s) for this game you can join."
                    )
                else:
                    messagebox.showwarning(
                        "Room Full",
                        f"This room has reached its capacity.\n\n"
                        f"💡 You can create a new room or wait for other players to leave."
                    )
            elif "already in room" in error.lower():
                messagebox.showinfo("Join Room", "You are already in another room. Please leave that room first.")
            else:
                messagebox.showwarning("Join Room", error)

    def leave_room(self) -> None:
        room = self.get_selected_room()
        if not room:
            messagebox.showwarning("Leave Room", "Select a room to leave")
            return
        try:
            resp = self.conn.call({"type": "LEAVE_ROOM", "roomId": room["id"]})
        except Exception as exc:
            messagebox.showerror("Leave Room", str(exc))
            return
        if resp.get("ok"):
            # 關閉聊天視窗
            self.close_room_chat_window(room["id"])
            messagebox.showinfo("Leave Room", "You have left the room")
            self.refresh_rooms()
        else:
            messagebox.showwarning("Leave Room", resp.get("error", "failed"))

    def open_room_chat(self) -> None:
        """
        打開房間聊天視窗 (Use Case PL3)
        只有安裝了 Room Chat Plugin 且在房間內的玩家才能使用
        """
        # 確認已登入
        if not self.player:
            messagebox.showwarning("Chat", "Please login first")
            return
        
        # 找到玩家所在的房間
        current_room = None
        current_members = []
        for item in self.rooms:
            members = item.get("members", [])
            if self.is_player_in_members(members):
                current_room = item["room"]
                current_members = members
                break
        
        if not current_room:
            messagebox.showwarning("Chat", "You haven't joined any room\n\nPlease join a room first to use chat")
            return
        
        room_id = current_room["id"]
        
        # 檢查是否已有聊天視窗
        if room_id in self.active_plugin_widgets:
            # 將視窗帶到前面
            widget_info = self.active_plugin_widgets[room_id]
            if widget_info.get("window"):
                try:
                    widget_info["window"].lift()
                    widget_info["window"].focus_force()
                    return
                except tk.TclError:
                    # 視窗已被關閉
                    del self.active_plugin_widgets[room_id]
        
        # 檢查是否安裝了 room_chat plugin
        if not self.is_plugin_installed("room_chat"):
            messagebox.showinfo(
                "需要安裝 Plugin",
                "您尚未安裝「Room Chat Plugin」\n\n"
                "請前往「Plugins」分頁安裝此 Plugin 後再使用。\n\n"
                "注意：這是可選功能，不安裝也可以正常遊戲！"
            )
            return
        
        # 載入 Plugin 模組
        self.load_all_installed_plugins()
        
        if "room_chat" not in self.loaded_plugin_modules:
            messagebox.showerror("Chat", "Failed to load Room Chat Plugin")
            return
        
        # 創建聊天視窗
        self.create_chat_window(room_id, current_room, current_members)

    def create_chat_window(self, room_id: int, room: Dict[str, Any], members: List[Dict[str, Any]]) -> None:
        """創建獨立的聊天視窗"""
        module = self.loaded_plugin_modules.get("room_chat")
        if not module:
            return
        
        # 創建 Toplevel 視窗
        chat_win = tk.Toplevel(self)
        room_code = room.get("code", str(room_id))
        chat_win.title(f"💬 Room Chat - {room_code}")
        chat_win.geometry("360x450")
        chat_win.transient(self)  # 設為主視窗的子視窗
        
        # 發送訊息的回調
        def send_chat_message(message: str):
            try:
                resp = self.conn.call({
                    "type": "ROOM_CHAT",
                    "roomId": room_id,
                    "message": message
                })
                if not resp.get("ok"):
                    print(f"[Chat] Send failed: {resp.get('error')}")
            except Exception as e:
                print(f"[Chat] Send error: {e}")
        
        # 創建聊天 Widget
        if hasattr(module, "create_widget"):
            widget = module.create_widget(
                chat_win,
                self.player.username if self.player else "Guest",
                room_id,
                send_callback=send_chat_message
            )
            widget.pack(fill="both", expand=True, padx=5, pady=5)
            
            # 顯示當前成員
            for member in members:
                if member.get("player_id") != self.player.id:
                    widget.player_joined(member.get("username", "Unknown"))
            
            # 載入歷史訊息
            loaded_msg_ids = self.load_chat_history(room_id, widget)
            
            # 儲存 Widget 資訊（包含已載入訊息的 ID 追蹤）
            self.active_plugin_widgets[room_id] = {
                "window": chat_win,
                "widget": widget,
                "type": "room_chat",
                "loaded_msg_ids": loaded_msg_ids  # 追蹤已載入的訊息 ID
            }
            
            # 視窗關閉時清理
            def on_close():
                if room_id in self.active_plugin_widgets:
                    del self.active_plugin_widgets[room_id]
                chat_win.destroy()
            
            chat_win.protocol("WM_DELETE_WINDOW", on_close)
            
            # 啟動定期刷新
            self.start_chat_refresh(room_id)
        else:
            chat_win.destroy()
            messagebox.showerror("Chat", "Plugin format error")

    def load_chat_history(self, room_id: int, widget: Any) -> set:
        """載入聊天歷史記錄，返回已載入的訊息 ID 集合"""
        loaded_ids = set()
        try:
            resp = self.conn.call({
                "type": "GET_ROOM_CHAT_HISTORY",
                "roomId": room_id,
                "limit": 50
            })
            if resp.get("ok"):
                messages = resp.get("messages", [])
                # 訊息是倒序的，需要反轉
                import time as time_module
                for msg in reversed(messages):
                    msg_id = msg.get("id")
                    if msg_id:
                        loaded_ids.add(msg_id)
                    
                    username = msg.get("username", "Unknown")
                    message = msg.get("message", "")
                    # 格式化時間
                    created_at = msg.get("created_at", 0)
                    ts = time_module.strftime("%H:%M", time_module.localtime(created_at)) if created_at else ""
                    
                    # 顯示所有歷史訊息（包括自己的）
                    widget.receive_message(username, message, ts)
        except Exception as e:
            print(f"[Chat] Load history error: {e}")
        return loaded_ids

    def start_chat_refresh(self, room_id: int) -> None:
        """啟動聊天定期刷新，即時獲取新訊息"""
        def refresh():
            if room_id not in self.active_plugin_widgets:
                return  # 視窗已關閉
            
            widget_info = self.active_plugin_widgets.get(room_id)
            if not widget_info or not widget_info.get("window"):
                return
            
            # 拉取新訊息
            try:
                resp = self.conn.call({
                    "type": "GET_ROOM_CHAT_HISTORY",
                    "roomId": room_id,
                    "limit": 50
                })
                if resp.get("ok"):
                    messages = resp.get("messages", [])
                    loaded_ids = widget_info.get("loaded_msg_ids", set())
                    widget = widget_info.get("widget")
                    
                    import time as time_module
                    # 找出新訊息（從舊到新顯示）
                    new_messages = []
                    for msg in messages:
                        msg_id = msg.get("id")
                        if msg_id and msg_id not in loaded_ids:
                            new_messages.append(msg)
                            loaded_ids.add(msg_id)
                    
                    # 反轉讓舊訊息先顯示
                    for msg in reversed(new_messages):
                        username = msg.get("username", "Unknown")
                        message = msg.get("message", "")
                        created_at = msg.get("created_at", 0)
                        ts = time_module.strftime("%H:%M", time_module.localtime(created_at)) if created_at else ""
                        
                        # 只顯示其他人的訊息（自己發送的已經本地顯示過了）
                        if username != (self.player.username if self.player else ""):
                            widget.receive_message(username, message, ts)
                    
                    widget_info["loaded_msg_ids"] = loaded_ids
            except Exception as e:
                print(f"[Chat] Refresh error: {e}")
            
            # 排程下次刷新（2秒）
            try:
                widget_info["window"].after(2000, refresh)
            except tk.TclError:
                pass  # 視窗已銷毀
        
        # 啟動第一次刷新（2秒後）
        widget_info = self.active_plugin_widgets.get(room_id)
        if widget_info and widget_info.get("window"):
            widget_info["window"].after(2000, refresh)

    def close_room_chat_window(self, room_id: int) -> None:
        """關閉指定房間的聊天視窗"""
        if room_id in self.active_plugin_widgets:
            widget_info = self.active_plugin_widgets.pop(room_id, None)
            if widget_info and widget_info.get("window"):
                try:
                    widget_info["window"].destroy()
                except tk.TclError:
                    pass

    def get_selected_room(self) -> Optional[Dict[str, Any]]:
        selection = self.room_tree.selection()
        if not selection:
            return None
        room_id = int(selection[0])
        for item in self.rooms:
            if item["room"]["id"] == room_id:
                return item["room"]
        return None

    def is_player_in_members(self, members: List[Dict[str, Any]]) -> bool:
        if not self.player:
            return False
        for member in members:
            if member.get("player_id") == self.player.id:
                return True
        return False

    def ensure_room_auto_launch(self, room: Dict[str, Any], members: List[Dict[str, Any]]) -> None:
        # print(f"[DEBUG] ensure_room_auto_launch room={room.get('id')} status={room.get('status')}")
        if not self.is_player_in_members(members):
            # print("[DEBUG] Player not in members")
            return
        status = (room.get("status") or "").lower()
        if status not in {"launching", "playing"}:
            # print(f"[DEBUG] Status {status} not launching/playing")
            return
        launch = self.fetch_launch_info(room["id"], silent=True)
        # print(f"[DEBUG] fetch_launch_info result: {launch}")
        if launch:
            self.handle_launch_payload(room, launch)

    def fetch_launch_info(self, room_id: int, silent: bool = False) -> Optional[Dict[str, Any]]:
        try:
            resp = self.conn.call({"type": "GET_GAME", "roomId": room_id})
        except Exception as exc:
            if not silent:
                messagebox.showerror("Launch", str(exc))
            return None
        if not resp.get("ok"):
            if not silent:
                messagebox.showwarning("Launch", resp.get("error", "Game not ready"))
            return None
        return resp.get("launch")

    def launch_library_key(self, launch: Dict[str, Any]) -> str:
        return f"{launch.get('gameId')}::{launch.get('gameVersionId')}"

    def make_launch_token(self, launch: Dict[str, Any]) -> str:
        token_raw = launch.get("roomToken") or launch.get("token") or ""
        return f"{launch.get('roomId')}::{token_raw}"

    def ensure_local_version(self, launch: Dict[str, Any], silent: bool = False) -> bool:
        """
        Ensure the player has the required game version installed.
        Will check for latest version and prompt for update if needed.
        """
        if not self.player:
            return False
        
        game_id = launch.get("gameId")
        version_id = launch.get("gameVersionId")
        
        if not game_id:
            if not silent:
                messagebox.showwarning("Launch", "Launch payload missing game identifiers.")
            return False
        
        # Check if we have the required version AND it's the latest
        is_ready, error_msg = self.check_game_version_requirement(game_id, version_id)
        
        if not is_ready:
            if not silent:
                if self.prompt_download_or_update(game_id, error_msg):
                    if self.download_game_for_play(game_id):
                        # Re-load library and check again
                        self.load_local_library()
                        is_ready, _ = self.check_game_version_requirement(game_id, version_id)
                        return is_ready
            return False
        
        return True

    def launch_game_payload(self, launch: Dict[str, Any], force: bool = False) -> None:
        # print(f"[DEBUG] launch_game_payload launch={launch} force={force}")
        if not launch:
            return
        token = self.make_launch_token(launch)
        
        # If force is True, we allow re-launching even if token is active
        if token and token in self.active_launch_tokens and not force:
            # print(f"[DEBUG] Token {token} already active")
            return
            
        ready = self.ensure_local_version(launch, silent=not force)
        # print(f"[DEBUG] ensure_local_version ready={ready}")
        if not ready:
            return
        key = self.launch_library_key(launch)
        item = self.local_library.get(key)
        if not item:
            # print(f"[DEBUG] Item not found in local_library for key {key}")
            if force:
                messagebox.showwarning("Launch", "Game version not installed. Download from the store first.")
            return
        version = item["version"]
        entrypoint = version.get("client_entrypoint")
        # print(f"[DEBUG] entrypoint={entrypoint}")
        if not entrypoint:
            if force:
                messagebox.showerror("Launch", "Client entrypoint missing in metadata")
            return
        env = os.environ.copy()
        env.update(
            {
                "GAME_HOST": str(launch.get("host")),
                "GAME_PORT": str(launch.get("port")),
                "GAME_ROOM_ID": str(launch.get("roomId")),
                "GAME_ROOM_TOKEN": launch.get("roomToken", ""),
                "PLAYER_ID": str(self.player.id if self.player else 0),
                "PLAYER_USERNAME": self.player.username if self.player else "",
            }
        )
        if "playerIndex" in launch:
            env["PLAYER_INDEX"] = str(launch["playerIndex"])
        creationflags = 0
        cmd_args = []
        use_shell = True
        
        # Prepare command arguments
        # We assume entrypoint is simple like "python client.py"
        parts = entrypoint.split()
        if parts[0] in ["python", "python3"]:
            parts[0] = sys.executable
            
        if version.get("client_mode", "gui") == "cli" and os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            # Use cmd /k to keep window open
            cmd_args = ["cmd.exe", "/k"] + parts
            use_shell = False # We are invoking cmd.exe directly
        else:
            cmd_args = parts
            use_shell = False # Safer to use list of args

        # print(f"[DEBUG] Launching: {cmd_args}")
        # print(f"[DEBUG] CWD: {item['bundle']}")
        
        if not item["bundle"].exists():
             # print(f"[ERROR] Bundle directory does not exist: {item['bundle']}")
             if force:
                 messagebox.showerror("Launch Failed", 
                     f"Game files missing!\n\n"
                     f"Path: {item['bundle']}\n\n"
                     f"Please try re-downloading the game.")
                 # Clear launch info to prevent stuck state
                 self.last_launch_info = None
             return

        try:
            process = subprocess.Popen(
                cmd_args,
                cwd=item["bundle"],
                shell=use_shell,
                env=env,
                creationflags=creationflags,
            )
            if token:
                self.active_launch_tokens.add(token)
            
            # Give the process a moment to start and check if it failed immediately
            time.sleep(0.5)
            if process.poll() is not None and process.returncode != 0:
                raise Exception(f"Process exited immediately with code {process.returncode}")
                
        except FileNotFoundError as exc:
            if force:
                messagebox.showerror("Launch Failed", 
                    f"Game executable not found\n\n"
                    f"Entry point: {entrypoint}\n"
                    f"Error: {exc}\n\n"
                    f"Please ensure the game is properly installed.")
                self.last_launch_info = None
        except Exception as exc:
            # print(f"[ERROR] Popen failed: {exc}")
            if force:
                messagebox.showerror("Launch Failed", 
                    f"Cannot start game\n\n"
                    f"Error: {exc}\n\n"
                    f"You can try:\n"
                    f"• Re-download the game\n"
                    f"• Check network connection\n"
                    f"• Contact the game developer")
                # Clear launch info to allow retry
                self.last_launch_info = None

    def record_launch_info(self, room: Dict[str, Any], launch: Dict[str, Any]) -> Dict[str, Any]:
        info = dict(launch)
        info.setdefault("gameId", room.get("game_id"))
        info.setdefault("gameVersionId", room.get("game_version_id"))
        players = launch.get("players")
        if players and self.player:
            for idx, meta in enumerate(players, start=1):
                if meta.get("playerId") == self.player.id:
                    info["playerIndex"] = idx
                    break
        self.last_launch_info = info
        return info

    def handle_launch_payload(self, room: Dict[str, Any], launch: Dict[str, Any], force: bool = False) -> None:
        info = self.record_launch_info(room, launch)
        self.launch_game_payload(info, force=force)

    def start_room_game(self) -> None:
        room = self.get_selected_room()
        if not room:
            messagebox.showwarning("Start Game", "Please select a room first")
            return
        
        # Check if player is the room owner
        if self.player and room.get("owner_player_id") != self.player.id:
            messagebox.showwarning("Start Game", "Only the host can start the game")
            return
        
        # Check minimum players
        room_id = room["id"]
        members = []
        for item in self.rooms:
            if item["room"]["id"] == room_id:
                members = item.get("members", [])
                break
        
        game_meta = self.game_index.get(room.get("game_id"), {})
        min_players = game_meta.get("min_players", 1)
        
        if len(members) < min_players:
            messagebox.showwarning(
                "人數不足", 
                f"This game requires at least {min_players} players to start.\n\n"
                f"Current players: {len(members)}/{min_players}"
            )
            return
        
        try:
            resp = self.conn.call({"type": "START_GAME", "roomId": room["id"]})
        except Exception as exc:
            messagebox.showerror("Start Game", f"Connection error: {exc}")
            return
        
        if not resp.get("ok"):
            error = resp.get("error", "failed")
            if "already" in error.lower() or "playing" in error.lower():
                messagebox.showinfo("Game In Progress", "Game is already in progress, trying to connect...")
                # Try to get launch info for ongoing game
                pending = self.fetch_launch_info(room["id"], silent=True)
                if pending:
                    self.handle_launch_payload(room, pending, force=True)
            elif "not enough" in error.lower() or "minimum" in error.lower():
                messagebox.showwarning("Not Enough Players", error)
            else:
                messagebox.showwarning("Start Failed", error)
            return
        
        launch = resp.get("launch", {})
        if launch:
            self.handle_launch_payload(room, launch, force=True)
        else:
            pending = self.fetch_launch_info(room["id"], silent=True)
            if pending:
                self.handle_launch_payload(room, pending, force=True)
            else:
                messagebox.showinfo(
                    "遊戲啟動中",
                    "遊戲伺服器正在啟動中...\n\n"
                    "準備就緒後將自動連線。",
                )
        self.refresh_rooms(silent=True)

    def retry_active_launch(self) -> None:
        room = self.get_selected_room()
        if not room:
            messagebox.showwarning("Launch", "Select a room")
            return
        launch = self.fetch_launch_info(room["id"])
        if not launch:
            return
        self.handle_launch_payload(room, launch, force=True)

    def submit_review_dialog(self) -> None:
        """
        Open a dialog to submit a review for the selected game.
        Implements Use Case P4 with validation and error handling.
        """
        game = self.get_selected_game()
        if not game:
            messagebox.showwarning("Review", "Please select a game to review first")
            return
        
        # 檢查玩家是否有下載過此遊戲
        game_id = game["id"]
        has_downloaded = False
        for slug, lib_data in self.local_library.items():
            if lib_data.get("game", {}).get("id") == game_id:
                has_downloaded = True
                break
        
        if not has_downloaded:
            messagebox.showwarning("Cannot Review", 
                f"You haven't downloaded '{game.get('title', 'this game')}'.\n\n"
                f"Please download and play this game before reviewing.")
            return
        
        # Create review dialog
        dialog = tk.Toplevel(self)
        dialog.title(f"Review Game - {game.get('title', 'Unknown')}")
        dialog.geometry("400x380")
        dialog.transient(self)
        dialog.grab_set()
        
        # Store draft for recovery on connection failure
        draft = {"rating": 3.0, "comment": ""}
        
        # Game info header
        header = ttk.Frame(dialog)
        header.pack(fill="x", padx=15, pady=10)
        ttk.Label(header, text=f"🎮 {game.get('title', 'Unknown')}", font=("", 12, "bold")).pack(anchor="w")
        
        # Rating section (支援小數點)
        rating_frame = ttk.LabelFrame(dialog, text="Rating (1.0 - 5.0)")
        rating_frame.pack(fill="x", padx=15, pady=5)
        
        rating_var = tk.DoubleVar(value=3.0)
        rating_display = ttk.Label(rating_frame, text="3.0 ★★★☆☆", font=("", 14))
        rating_display.pack(pady=5)
        
        def update_rating_display(val):
            r = round(float(val), 1)
            rating_var.set(r)
            draft["rating"] = r
            full_stars = int(r)
            half = (r - full_stars) >= 0.5
            empty_stars = 5 - full_stars - (1 if half else 0)
            stars = "★" * full_stars + ("½" if half else "") + "☆" * empty_stars
            rating_display.config(text=f"{r:.1f} {stars}")
        
        rating_scale = ttk.Scale(rating_frame, from_=1.0, to=5.0, orient="horizontal",
                                  command=update_rating_display)
        rating_scale.set(3.0)
        rating_scale.pack(fill="x", padx=10, pady=5)
        
        # Quick rating buttons
        quick_frame = ttk.Frame(rating_frame)
        quick_frame.pack(fill="x", padx=10, pady=5)
        for r in [1.0, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]:
            btn = ttk.Button(quick_frame, text=str(r), width=4,
                           command=lambda x=r: (rating_scale.set(x), update_rating_display(x)))
            btn.pack(side="left", expand=True, padx=1)
        
        # Comment section
        comment_frame = ttk.LabelFrame(dialog, text="Comment (optional, max 1000 chars)")
        comment_frame.pack(fill="both", expand=True, padx=15, pady=5)
        
        comment_text = tk.Text(comment_frame, height=6, wrap="word")
        comment_text.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Character count
        char_count_var = tk.StringVar(value="0/1000 chars")
        char_count_label = ttk.Label(comment_frame, textvariable=char_count_var)
        char_count_label.pack(anchor="e", padx=5)
        
        def update_char_count(event=None):
            text = comment_text.get("1.0", tk.END).strip()
            draft["comment"] = text
            count = len(text)
            char_count_var.set(f"{count}/1000 chars")
            if count > 1000:
                char_count_label.config(foreground="red")
            else:
                char_count_label.config(foreground="")
        
        comment_text.bind("<KeyRelease>", update_char_count)
        
        # Status label for errors
        status_var = tk.StringVar(value="")
        status_label = ttk.Label(dialog, textvariable=status_var, foreground="red")
        status_label.pack(padx=15, pady=2)
        
        # Button frame
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill="x", padx=15, pady=10)
        
        result = {"submitted": False}
        
        def submit():
            rating = rating_var.get()
            comment = comment_text.get("1.0", tk.END).strip()
            
            # Client-side validation
            if not (1.0 <= rating <= 5.0):
                status_var.set("❌ Rating must be between 1.0-5.0")
                return
            
            if len(comment) > 1000:
                status_var.set(f"❌ Comment too long, please remove {len(comment) - 1000} chars")
                return
            
            status_var.set("⏳ Submitting...")
            dialog.update()
            
            try:
                resp = self.conn.call({
                    "type": "SUBMIT_REVIEW",
                    "gameId": game["id"],
                    "rating": rating,
                    "comment": comment,
                })
            except Exception as exc:
                status_var.set(f"❌ Connection failed: {exc}")
                messagebox.showerror("Connection Error", 
                    f"Error submitting review:\n{exc}\n\n"
                    f"Your review content has been saved, please try again later.")
                return
            
            if resp.get("ok"):
                result["submitted"] = True
                dialog.destroy()
                messagebox.showinfo("Review Submitted", 
                    f"✅ Thank you for your review!\n\n"
                    f"Rating: {rating:.1f} stars\n"
                    f"{'Comment: ' + comment[:50] + '...' if len(comment) > 50 else ('Comment: ' + comment if comment else '')}")
                self.refresh_games()
            else:
                error = resp.get("error", "Submit failed")
                status_var.set(f"❌ {error}")
                if "not played" in error.lower() or "not downloaded" in error.lower() or "尚未遊玩" in error or "尚未下載" in error:
                    messagebox.showwarning("Cannot Review", 
                        f"{error}\n\n"
                        f"Please download and play this game before reviewing.")
                elif "too long" in error.lower() or "過長" in error:
                    messagebox.showwarning("Comment Too Long", error)
                else:
                    messagebox.showwarning("Review Failed", error)
        
        def cancel():
            if draft["comment"].strip():
                if messagebox.askyesno("Cancel Review", "Are you sure you want to cancel?\nYour review content will not be saved."):
                    dialog.destroy()
            else:
                dialog.destroy()
        
        ttk.Button(btn_frame, text="Submit Review", command=submit).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=cancel).pack(side="right", padx=5)
        
        # Handle window close
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        
        self.wait_window(dialog)

    def refresh_plugins(self) -> None:
        """刷新 Plugin 列表 (Use Case PL1)"""
        try:
            resp = self.conn.call({"type": "PLUGIN_LIST"})
        except Exception as exc:
            messagebox.showerror("Plugins", f"Failed to get plugin list: {exc}")
            return
        if not resp.get("ok"):
            messagebox.showwarning("Plugins", resp.get("error", "failed"))
            return
        
        self.plugins = resp.get("plugins", [])
        installed = resp.get("installed", [])
        self.installed_plugins = installed
        
        # 建立已安裝 Plugin 的映射
        installed_map = {item["slug"]: item for item in installed}
        
        # 更新列表顯示
        self.plugin_tree.delete(*self.plugin_tree.get_children())
        
        for plugin in self.plugins:
            slug = plugin["slug"]
            name = plugin.get("name", slug)
            latest_version = plugin.get("latest_version", "?")
            
            # 判斷狀態
            if slug in installed_map:
                installed_info = installed_map[slug]
                installed_version = installed_info.get("installed_version", "?")
                if installed_version == latest_version:
                    status = "✅ 已安裝"
                else:
                    status = "🔄 可更新"
            else:
                status = "📭 未安裝"
            
            self.plugin_tree.insert(
                "",
                tk.END,
                iid=slug,
                values=(name, latest_version, status),
            )
        
        # 更新詳情顯示
        self.show_plugin_details()
    
    def show_plugin_details(self) -> None:
        """顯示選中 Plugin 的詳細資訊"""
        self.plugin_details.configure(state="normal")
        self.plugin_details.delete("1.0", tk.END)
        
        selection = self.plugin_tree.selection()
        if not selection:
            self.plugin_details.insert(tk.END, 
                "👈 Select a plugin from the list to view details\n\n"
                "Plugins provide additional features, such as:\n"
                "• In-room chat\n"
                "• Game statistics\n"
                "• Other extensions\n\n"
                "⚠️ Even without any plugins installed,\n"
                "you can still use all game features normally.")
            self.plugin_details.configure(state="disabled")
            return
        
        slug = selection[0]
        plugin = next((p for p in self.plugins if p["slug"] == slug), None)
        if not plugin:
            self.plugin_details.insert(tk.END, "Plugin info unavailable")
            self.plugin_details.configure(state="disabled")
            return
        
        # 檢查安裝狀態
        installed_map = {item["slug"]: item for item in self.installed_plugins}
        is_installed = slug in installed_map
        installed_info = installed_map.get(slug)
        
        lines = []
        lines.append(f"🔌 {plugin.get('name', slug)}")
        lines.append("")
        lines.append(f"📋 Slug: {slug}")
        lines.append(f"📦 Latest Version: v{plugin.get('latest_version', '?')}")
        
        if is_installed:
            installed_ver = installed_info.get("installed_version", "?")
            installed_at = installed_info.get("installed_at", "unknown")
            lines.append(f"✅ Installed Version: v{installed_ver}")
            lines.append(f"📅 Installed At: {installed_at}")
            
            if installed_ver != plugin.get("latest_version"):
                lines.append("")
                lines.append("🆕 New version available! Click 'Install' to update.")
        else:
            lines.append("📭 Status: Not Installed")
        
        lines.append("")
        lines.append("─" * 35)
        lines.append("📝 Description:")
        description = plugin.get("description", "").strip()
        if description:
            lines.append(description)
        else:
            lines.append("(No description)")
        
        # 顯示 Package 資訊
        lines.append("")
        lines.append("─" * 35)
        lines.append("📊 Package Info:")
        size = plugin.get("package_size", 0)
        if size > 1024 * 1024:
            size_str = f"{size / 1024 / 1024:.1f} MB"
        elif size > 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size} bytes"
        lines.append(f"  Size: {size_str}")
        
        self.plugin_details.insert(tk.END, "\n".join(lines))
        self.plugin_details.configure(state="disabled")
    
    def get_selected_plugin_slug(self) -> Optional[str]:
        """取得選中的 Plugin slug"""
        selection = self.plugin_tree.selection()
        return selection[0] if selection else None

    def install_plugin_action(self) -> None:
        """安裝 Plugin (Use Case PL2)"""
        slug = self.get_selected_plugin_slug()
        if not slug:
            messagebox.showwarning("Plugin", "Please select a plugin to install first")
            return
        
        # 取得 Plugin 資訊
        plugin = next((p for p in self.plugins if p["slug"] == slug), None)
        if not plugin:
            messagebox.showerror("Plugin", "Plugin information not found")
            return
        
        # 確認安裝
        installed_map = {item["slug"]: item for item in self.installed_plugins}
        is_update = slug in installed_map
        
        if is_update:
            result = messagebox.askquestion(
                "Update Plugin",
                f"Do you want to update {plugin.get('name', slug)}?\n\n"
                f"Current version: v{installed_map[slug].get('installed_version', '?')}\n"
                f"Latest version: v{plugin.get('latest_version', '?')}"
            )
        else:
            result = messagebox.askquestion(
                "Install Plugin",
                f"Do you want to install {plugin.get('name', slug)}?\n\n"
                f"{plugin.get('description', '(No description)')}"
            )
        
        if result != "yes":
            return
        
        # 顯示進度
        progress_win = tk.Toplevel(self)
        progress_win.title("Installing...")
        progress_win.geometry("250x80")
        progress_win.transient(self)
        progress_win.grab_set()
        ttk.Label(progress_win, text=f"Installing {plugin.get('name', slug)}...").pack(pady=20)
        progress_win.update()
        
        try:
            resp = self.conn.call({"type": "PLUGIN_INSTALL", "slug": slug})
        except Exception as exc:
            progress_win.destroy()
            messagebox.showerror("Plugin", f"Installation failed: {exc}\n\nMain system functions are not affected.")
            return
        
        if not resp.get("ok"):
            progress_win.destroy()
            messagebox.showwarning("Plugin", f"Installation failed: {resp.get('error', 'failed')}\n\nMain system functions are not affected.")
            return
        
        plugin_data = resp["plugin"]
        payload = resp.get("package")
        
        try:
            install_plugin(ensure_dir(PLUGIN_ROOT / self.player.username), plugin_data, payload)
            # 載入 Plugin 模組
            self.load_plugin_module(slug)
        except Exception as exc:
            progress_win.destroy()
            messagebox.showerror("Plugin", f"Installation failed: {exc}\n\nMain system functions are not affected.")
            return
        
        progress_win.destroy()
        action = "updated" if is_update else "installed"
        messagebox.showinfo("Plugin", f"✅ {plugin_data['name']} {action} successfully!\n\nVersion: v{plugin_data.get('latest_version', '?')}")
        self.refresh_plugins()

    def remove_plugin_action(self) -> None:
        """移除 Plugin (Use Case PL2)"""
        slug = self.get_selected_plugin_slug()
        if not slug:
            messagebox.showwarning("Plugin", "Please select a plugin to remove first")
            return
        
        # 檢查是否已安裝
        installed_map = {item["slug"]: item for item in self.installed_plugins}
        if slug not in installed_map:
            messagebox.showwarning("Plugin", "This plugin is not installed")
            return
        
        plugin = next((p for p in self.plugins if p["slug"] == slug), None)
        plugin_name = plugin.get("name", slug) if plugin else slug
        
        # 確認移除
        result = messagebox.askquestion(
            "Remove Plugin",
            f"Do you want to remove {plugin_name}?\n\n"
            f"You won't be able to use this plugin's features after removal,\n"
            f"but it won't affect normal gameplay."
        )
        
        if result != "yes":
            return
        
        try:
            resp = self.conn.call({"type": "PLUGIN_REMOVE", "slug": slug})
        except Exception as exc:
            messagebox.showerror("Plugin", f"Removal failed: {exc}")
            return
        
        if not resp.get("ok"):
            messagebox.showwarning("Plugin", resp.get("error", "failed"))
            return
        
        # 移除本地檔案
        folder = PLUGIN_ROOT / self.player.username / slug
        if folder.exists():
            shutil.rmtree(folder)
        
        # 卸載模組
        if slug in self.loaded_plugin_modules:
            del self.loaded_plugin_modules[slug]
        
        messagebox.showinfo("Plugin", f"✅ {plugin_name} has been removed")
        self.refresh_plugins()
    
    def load_plugin_module(self, slug: str) -> Optional[Any]:
        """載入 Plugin 模組"""
        if not self.player:
            return None
        
        try:
            plugin_dir = PLUGIN_ROOT / self.player.username / slug / "bundle"
            if not plugin_dir.exists():
                return None
            
            # 檢查 metadata
            metadata_path = PLUGIN_ROOT / self.player.username / slug / "metadata.json"
            if not metadata_path.exists():
                return None
            
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            client_entry = metadata.get("client_entry", "chat_widget.py")
            
            # 動態載入模組
            import importlib.util
            module_path = plugin_dir / client_entry
            if not module_path.exists():
                return None
            
            spec = importlib.util.spec_from_file_location(f"plugin_{slug}", module_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.loaded_plugin_modules[slug] = module
                return module
        except Exception as e:
            print(f"[Plugin] Failed to load {slug}: {e}")
        return None
    
    def load_all_installed_plugins(self) -> None:
        """載入所有已安裝的 Plugin"""
        if not self.player:
            return
        
        plugin_root = PLUGIN_ROOT / self.player.username
        if not plugin_root.exists():
            return
        
        for plugin_dir in plugin_root.iterdir():
            if plugin_dir.is_dir():
                slug = plugin_dir.name
                if slug not in self.loaded_plugin_modules:
                    self.load_plugin_module(slug)
    
    def is_plugin_installed(self, slug: str) -> bool:
        """檢查 Plugin 是否已安裝"""
        return any(p["slug"] == slug for p in self.installed_plugins)
    
    def get_plugin_widget_for_room(self, parent, room_id: int) -> Optional[tk.Widget]:
        """
        取得房間用的 Plugin Widget (Use Case PL3)
        如果玩家安裝了聊天 Plugin，返回聊天 Widget
        """
        # 確保已載入 Plugin
        self.load_all_installed_plugins()
        
        # 檢查是否安裝了 room_chat plugin
        if "room_chat" in self.loaded_plugin_modules:
            try:
                module = self.loaded_plugin_modules["room_chat"]
                if hasattr(module, "create_widget"):
                    # 創建聊天發送回調
                    def send_chat_message(message: str):
                        try:
                            self.conn.call({
                                "type": "ROOM_CHAT",
                                "roomId": room_id,
                                "message": message
                            })
                        except Exception as e:
                            print(f"[Chat] Send failed: {e}")
                    
                    widget = module.create_widget(
                        parent,
                        self.player.username if self.player else "Guest",
                        room_id,
                        send_callback=send_chat_message
                    )
                    return widget
            except Exception as e:
                print(f"[Plugin] Failed to create widget: {e}")
        
        return None

def simple_prompt(parent: tk.Tk, title: str, label: str, initial: str = "") -> Optional[str]:
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.grab_set()

    var = tk.StringVar(value=initial)
    ttk.Label(dialog, text=label).pack(padx=10, pady=6)
    entry = ttk.Entry(dialog, textvariable=var)
    entry.pack(padx=10, pady=6, fill="x")
    entry.focus_set()
    result: Dict[str, Optional[str]] = {"value": None}

    def accept() -> None:
        result["value"] = var.get().strip()
        dialog.destroy()

    ttk.Button(dialog, text="OK", command=accept).pack(pady=10)
    parent.wait_window(dialog)
    return result["value"]


if __name__ == "__main__":
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)
    app = PlayerApp()
    app.mainloop()
