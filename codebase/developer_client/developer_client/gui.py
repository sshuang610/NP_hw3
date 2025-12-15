"""
開發者客戶端 GUI (Developer Client)
使用 Tkinter 建構的開發者控制台

功能模組:
- 帳號系統 (D1): 開發者註冊/登入
- 遊戲管理 (D2): 建立遊戲、上傳版本、設定狀態
- 遊戲更新 (D3): 更新遊戲資訊

主要 Class:
- DeveloperApp: 主視窗，包含所有 UI 邏輯
- DeveloperConnection: 與 Developer Server 的網路連線封裝
- DeveloperInfo: 開發者資訊 dataclass

網路通訊:
- 使用 Length-Prefixed JSON Protocol (common/lp.py)
- 連接到 Developer Server (預設 port 23001)

作者: HW3 作業
"""
import base64
import hashlib
import json
import os
import socket
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

from common.lp import recv_json, send_json


@dataclass
class DeveloperInfo:
    """開發者資訊，登入成功後由 Server 回傳"""
    id: int
    username: str


class DeveloperConnection:
    """
    Developer Server 連線管理
    - Thread-safe 的 socket 封裝
    - 提供 call() 方法進行 Request-Response 通訊
    """
    def __init__(self) -> None:
        self.sock: Optional[socket.socket] = None
        self.lock = threading.Lock()

    def connect(self, host: str, port: int) -> None:
        """建立 TCP 連線到 Developer Server"""
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
                raise RuntimeError("connection not established")
            send_json(self.sock, payload)
            return recv_json(self.sock)


def hash_password(password: str) -> str:
    """密碼 SHA256 雜湊，不以明文傳輸"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def zip_directory(path: Path) -> bytes:
    """
    將目錄打包成 ZIP 格式
    用於上傳遊戲版本
    """
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in path.rglob("*"):
            if file_path.is_file():
                arc = file_path.relative_to(path)
                zf.write(file_path, arcname=str(arc))
    return buffer.getvalue()


class DeveloperApp(tk.Tk):
    """
    開發者控制台主視窗
    
    使用 Tkinter 建構，包含:
    - 登入/註冊頁面
    - 遊戲管理 Dashboard
    
    主要功能:
    - D1: 開發者帳號註冊/登入
    - D2: 建立遊戲、上傳版本
    - D3: 更新遊戲資訊、設定狀態
    """
    def __init__(self) -> None:
        super().__init__()
        self.title("Game Store Developer Console")
        self.geometry("980x720")
        
        # 設定視窗關閉處理 (異常關閉時自動登出)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # 網路連線
        self.conn = DeveloperConnection()
        self.developer: Optional[DeveloperInfo] = None
        
        # 遊戲資料
        self.games: List[Dict[str, Any]] = []
        self.versions: Dict[int, List[Dict[str, Any]]] = {}
        
        # UI 變數
        self.host_var = tk.StringVar(value="127.0.0.1")
        self.port_var = tk.IntVar(value=23001)

        # 建構 UI
        self._build_login_frame()
        self._build_dashboard_frame()
        self.show_login()

    # UI setup -------------------------------------------------
    def _build_login_frame(self) -> None:
        self.login_frame = ttk.Frame(self)
        ttk.Label(self.login_frame, text="Developer Server Host").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(self.login_frame, textvariable=self.host_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(self.login_frame, text="Port").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(self.login_frame, textvariable=self.port_var).grid(row=1, column=1, sticky="ew", padx=8)

        ttk.Label(self.login_frame, text="Username").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self.username_entry = ttk.Entry(self.login_frame)
        self.username_entry.grid(row=2, column=1, sticky="ew", padx=8)

        ttk.Label(self.login_frame, text="Password").grid(row=3, column=0, sticky="w", padx=8, pady=4)
        self.password_entry = ttk.Entry(self.login_frame, show="*")
        self.password_entry.grid(row=3, column=1, sticky="ew", padx=8, pady=4)

        # Display Name removed as per requirements
        # ttk.Label(self.login_frame, text="Display Name:").grid(row=4, column=0, sticky="e", padx=6, pady=6)
        # self.display_entry = ttk.Entry(self.login_frame)
        # self.display_entry.grid(row=4, column=1, sticky="ew", padx=6, pady=6)

        btn_row = ttk.Frame(self.login_frame)
        btn_row.grid(row=5, column=0, columnspan=2, pady=12)
        ttk.Button(btn_row, text="Register", command=self.register).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="Login", command=self.login).pack(side=tk.LEFT, padx=6)

        self.login_frame.columnconfigure(1, weight=1)

    def _build_dashboard_frame(self) -> None:
        self.dashboard = ttk.Frame(self)
        self.developer_label = ttk.Label(self.dashboard, text="")
        self.developer_label.pack(anchor="w", padx=10, pady=6)

        toolbar = ttk.Frame(self.dashboard)
        toolbar.pack(fill="x", padx=10, pady=4)
        ttk.Button(toolbar, text="Refresh", command=self.refresh_games).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="📦 Upload New Game", command=self.upload_new_game_dialog).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Upload Version", command=self.upload_version_dialog).pack(side=tk.LEFT, padx=4)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=8, fill="y", pady=2)
        ttk.Button(toolbar, text="✅ Publish", command=lambda: self.update_status("published")).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="📝 Unpublish", command=lambda: self.update_status("draft")).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="🚫 Retire", command=lambda: self.update_status("retired")).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="🗑️ Delete", command=self.delete_game).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Logout", command=self.logout).pack(side=tk.RIGHT, padx=4)

        columns = ("id", "title", "status", "latest")
        self.game_tree = ttk.Treeview(self.dashboard, columns=columns, show="headings", height=15)
        for col in columns:
            self.game_tree.heading(col, text=col.capitalize())
            self.game_tree.column(col, width=160, anchor="center")
        self.game_tree.pack(fill="both", expand=True, padx=10, pady=6)
        self.game_tree.bind("<<TreeviewSelect>>", lambda _event: self.show_game_details())

        self.details_text = tk.Text(self.dashboard, height=10, state="disabled", wrap="word")
        self.details_text.pack(fill="both", expand=True, padx=10, pady=6)

    # View switching ------------------------------------------
    def show_login(self) -> None:
        self.dashboard.pack_forget()
        self.login_frame.pack(fill="both", expand=True, padx=20, pady=20)

    def show_dashboard(self) -> None:
        self.login_frame.pack_forget()
        self.dashboard.pack(fill="both", expand=True)

    # Actions --------------------------------------------------
    def ensure_connection(self) -> bool:
        host = self.host_var.get().strip()
        port = int(self.port_var.get())
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.conn.connect(host, port)
                return True
            except Exception as exc:
                if attempt < max_retries - 1:
                    retry = messagebox.askretrycancel(
                        "Connection Failed", 
                        f"Failed to connect to server: {exc}\n\n"
                        f"Attempt {attempt + 1} of {max_retries}.\n"
                        f"Host: {host}:{port}\n\n"
                        "Retry connection?"
                    )
                    if not retry:
                        return False
                else:
                    messagebox.showerror(
                        "Connection Failed", 
                        f"Could not connect after {max_retries} attempts.\n\n"
                        f"Error: {exc}\n\n"
                        "Please check:\n"
                        "• Server is running\n"
                        "• Host and port are correct\n"
                        "• Network connectivity"
                    )
                    return False
        return False

    def register(self) -> None:
        if not self.ensure_connection():
            return
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        # display = self.display_entry.get().strip() or username
        display = username # Use username as display name
        try:
            resp = self.conn.call(
                {
                    "type": "REGISTER",
                    "username": username,
                    "passwordHash": hash_password(password),
                    "displayName": display,
                }
            )
        except Exception as exc:
            messagebox.showerror("Register Failed", str(exc))
            return
        if resp.get("ok"):
            messagebox.showinfo("Registered", "Registration successful. Please login.")
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
            messagebox.showerror("Login Failed", str(exc))
            return
        if not resp.get("ok"):
            messagebox.showwarning("Login", resp.get("error", "failed"))
            return
        dev = resp.get("developer")
        self.developer = DeveloperInfo(dev["id"], dev["username"])
        self.developer_label.config(text=f"Logged in as {self.developer.username}")
        self.show_dashboard()
        self.refresh_games()

    def on_close(self) -> None:
        """視窗關閉事件處理 - 自動執行登出清理"""
        self._cleanup_and_logout()
        self.destroy()

    def _cleanup_and_logout(self) -> None:
        """清理資源並登出"""
        try:
            if self.developer:  # 只有已登入才需要登出
                self.conn.call({"type": "LOGOUT"})
        except Exception:
            pass  # 忽略錯誤，可能已斷線
        self.conn.close()
        self.developer = None

    def logout(self) -> None:
        """登出並返回登入畫面"""
        self._cleanup_and_logout()
        self.show_login()

    def refresh_games(self) -> None:
        try:
            resp = self.conn.call({"type": "LIST_GAMES"})
        except Exception as exc:
            messagebox.showerror("Refresh Failed", str(exc))
            return
        if not resp.get("ok"):
            messagebox.showwarning("Refresh", resp.get("error", "failed"))
            return
        self.games = resp.get("games", [])
        raw_versions = resp.get("versions", {})
        # Convert string keys to int keys if needed
        self.versions = {}
        for k, v in raw_versions.items():
            try:
                self.versions[int(k)] = v
            except (ValueError, TypeError):
                self.versions[k] = v
        self.game_tree.delete(*self.game_tree.get_children())
        for game in self.games:
            latest_id = game.get("latest_version_id")
            latest_version = "-"
            if latest_id:
                versions = self.versions.get(game["id"], [])
                for ver in versions:
                    if ver["id"] == latest_id:
                        latest_version = ver["version_label"]
                        break
            self.game_tree.insert(
                "",
                tk.END,
                iid=str(game["id"]),
                values=(game["id"], game["title"], game["status"], latest_version),
            )
        self.show_game_details()

    def get_selected_game(self) -> Optional[Dict[str, Any]]:
        selection = self.game_tree.selection()
        if not selection:
            return None
        game_id = int(selection[0])
        for game in self.games:
            if game["id"] == game_id:
                return game
        return None

    def show_game_details(self) -> None:
        game = self.get_selected_game()
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", tk.END)
        if not game:
            self.details_text.insert(tk.END, "Select a game to inspect details.")
            self.details_text.configure(state="disabled")
            return
        
        lines = []
        lines.append(f"📦 {game['title']}")
        lines.append(f"📝 {game['summary'] or '(No description)'}")
        lines.append(f"🏷️ Category: {game['category']}")
        lines.append(f"📊 Status: {game['status'].upper()}")
        lines.append(f"👥 Players: {game['min_players']} - {game['max_players']}")
        lines.append(f"🖥️ Supports: {'CLI ' if game['support_cli'] else ''}{'GUI' if game['support_gui'] else ''}")
        
        versions = self.versions.get(game["id"], [])
        if versions:
            lines.append("")
            lines.append("─" * 35)
            lines.append("📁 Versions:")
            for v in versions:
                lines.append(f"  • v{v['version_label']} ({v['client_mode']})")
        else:
            lines.append("\n⚠️ No versions uploaded.")

        # Fetch and display reviews
        try:
            resp = self.conn.call({"type": "GET_GAME_REVIEWS", "gameId": game["id"]})
            if resp.get("ok"):
                reviews = resp.get("reviews", [])
                lines.append("")
                lines.append("─" * 35)
                
                if reviews:
                    # Calculate average rating
                    avg_rating = sum(r.get("rating", 0) for r in reviews) / len(reviews)
                    lines.append(f"⭐ 玩家評價: {avg_rating:.1f}/5 ({len(reviews)} 則評論)")
                    lines.append("")
                    
                    # Show recent reviews
                    for review in reviews[:5]:
                        reviewer = review.get("player_name") or "Anonymous"
                        rating = review.get("rating", 0)
                        comment = (review.get("comment") or "").strip()
                        created_at = review.get("created_at", "")
                        
                        stars = "★" * rating + "☆" * (5 - rating)
                        lines.append(f"  {stars} by @{reviewer}")
                        if comment:
                            # Show comment (truncated if too long)
                            display_comment = comment[:80] + "..." if len(comment) > 80 else comment
                            lines.append(f"    \"{display_comment}\"")
                        lines.append("")
                    
                    if len(reviews) > 5:
                        lines.append(f"  ... 還有 {len(reviews) - 5} 則評論")
                else:
                    lines.append("💬 玩家評價:")
                    lines.append("  (尚無評論)")
        except Exception:
            # Silently ignore if reviews API fails
            pass

        self.details_text.insert(tk.END, "\n".join(lines))
        self.details_text.configure(state="disabled")

    def upload_version_dialog(self) -> None:
        """
        Upload a new version for an existing game.
        Implements Use Case D2: 開發者更新已上架遊戲版本
        """
        game = self.get_selected_game()
        if not game:
            messagebox.showwarning("Upload Version", "Please select a game from the list first.")
            return
        
        # Get existing versions for this game
        existing_versions = self.versions.get(game["id"], [])
        current_version = None
        if game.get("latest_version_id"):
            for v in existing_versions:
                if v["id"] == game["latest_version_id"]:
                    current_version = v
                    break
        
        directory = filedialog.askdirectory(title=f"Select New Version Directory for '{game['title']}'")
        if not directory:
            return
        path = Path(directory)
        
        # Check if directory exists and is readable
        if not path.exists():
            messagebox.showerror("Directory Error", f"The selected directory does not exist:\n{path}")
            return
        if not path.is_dir():
            messagebox.showerror("Directory Error", f"The selected path is not a directory:\n{path}")
            return
        
        # Check if directory contains any files
        files_in_dir = list(path.rglob("*"))
        if not any(f.is_file() for f in files_in_dir):
            messagebox.showerror("Directory Error", "The selected directory is empty or contains no files.")
            return
        
        manifest_data: Dict[str, Any] = {}
        manifest_path = path / "manifest.json"
        if manifest_path.exists():
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as exc:
                messagebox.showwarning("Manifest", f"Failed to parse manifest.json: {exc}. Proceeding with manual input.")
                manifest_data = {}
            else:
                if not isinstance(manifest_data, dict):
                    messagebox.showwarning("Manifest", "manifest.json must contain a JSON object. Ignoring file.")
                    manifest_data = {}
        else:
            manifest_data = {}

        # Build version dialog
        dialog = tk.Toplevel(self)
        dialog.title(f"Upload New Version - {game['title']}")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("480x500")
        
        # Show current version info
        info_frame = ttk.LabelFrame(dialog, text="Current Game Info", padding=10)
        info_frame.pack(fill="x", padx=10, pady=5)
        
        ttk.Label(info_frame, text=f"Game: {game['title']}", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(info_frame, text=f"Status: {game['status'].upper()}").pack(anchor="w")
        if current_version:
            ttk.Label(info_frame, text=f"Current Version: {current_version['version_label']}").pack(anchor="w")
        else:
            ttk.Label(info_frame, text="Current Version: (none)", foreground="gray").pack(anchor="w")
        
        # Show existing versions
        if existing_versions:
            ver_list = ", ".join([v["version_label"] for v in existing_versions[-5:]])
            ttk.Label(info_frame, text=f"Existing Versions: {ver_list}", foreground="gray").pack(anchor="w")
        
        # New version info
        ver_frame = ttk.LabelFrame(dialog, text="New Version Information", padding=10)
        ver_frame.pack(fill="x", padx=10, pady=5)
        
        version_default = manifest_data.get("version", "1.0.0")
        client_entry_default = manifest_data.get("clientEntrypoint", "python client.py")
        server_entry_default = manifest_data.get("serverEntrypoint", "python server.py")
        client_mode_default = manifest_data.get("clientMode", "gui")
        changelog_default = manifest_data.get("changelog") or manifest_data.get("description", "") or ""
        
        # If current version exists, suggest incrementing it
        if current_version:
            cur_ver = current_version["version_label"]
            try:
                parts = cur_ver.split(".")
                if len(parts) >= 3:
                    parts[-1] = str(int(parts[-1]) + 1)
                    version_default = ".".join(parts)
            except (ValueError, IndexError):
                pass
        
        version_var = tk.StringVar(value=version_default)
        client_entry_var = tk.StringVar(value=client_entry_default)
        server_entry_var = tk.StringVar(value=server_entry_default)
        client_mode_var = tk.StringVar(value=str(client_mode_default).lower())
        changelog_var = tk.StringVar(value=str(changelog_default))
        
        def add_row(parent: tk.Widget, row: int, label: str, widget: tk.Widget, required: bool = False) -> None:
            lbl_text = f"{label} *" if required else label
            ttk.Label(parent, text=lbl_text).grid(row=row, column=0, sticky="w", padx=4, pady=2)
            widget.grid(row=row, column=1, sticky="ew", padx=4, pady=2)
        
        add_row(ver_frame, 0, "Version Label", ttk.Entry(ver_frame, textvariable=version_var), required=True)
        add_row(ver_frame, 1, "Client Entrypoint", ttk.Entry(ver_frame, textvariable=client_entry_var), required=True)
        add_row(ver_frame, 2, "Server Entrypoint", ttk.Entry(ver_frame, textvariable=server_entry_var), required=True)
        add_row(ver_frame, 3, "Client Mode", ttk.Combobox(ver_frame, textvariable=client_mode_var, values=["cli", "gui"], state="readonly"))
        add_row(ver_frame, 4, "Changelog", ttk.Entry(ver_frame, textvariable=changelog_var))
        
        ver_frame.columnconfigure(1, weight=1)
        
        # Directory info
        dir_frame = ttk.Frame(dialog)
        dir_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(dir_frame, text=f"📁 Source: {path}", font=("Segoe UI", 8), foreground="gray").pack(anchor="w")
        
        # Status
        status_var = tk.StringVar(value="")
        status_label = ttk.Label(dialog, textvariable=status_var, foreground="blue")
        status_label.pack(anchor="w", padx=15, pady=5)
        
        def submit() -> None:
            # Validate
            version_label = version_var.get().strip()
            if not version_label:
                messagebox.showwarning("Validation Error", "Version label is required.", parent=dialog)
                return
            
            # Check for duplicate version
            for v in existing_versions:
                if v["version_label"] == version_label:
                    messagebox.showwarning("Validation Error", 
                        f"Version '{version_label}' already exists.\nPlease use a different version label.", 
                        parent=dialog)
                    return
            
            client_entry = client_entry_var.get().strip()
            if not client_entry:
                messagebox.showwarning("Validation Error", "Client entrypoint is required.", parent=dialog)
                return
            
            server_entry = server_entry_var.get().strip()
            if not server_entry:
                messagebox.showwarning("Validation Error", "Server entrypoint is required.", parent=dialog)
                return
            
            client_mode = client_mode_var.get() or "gui"
            if client_mode not in ("cli", "gui"):
                messagebox.showwarning("Validation Error", "Client mode must be 'cli' or 'gui'.", parent=dialog)
                return
            
            # Package files
            status_var.set("Packaging files...")
            dialog.update()
            
            try:
                data = zip_directory(path)
            except Exception as exc:
                status_var.set("")
                messagebox.showerror("Packaging Failed", f"Failed to package files:\n{exc}", parent=dialog)
                return
            
            status_var.set(f"Uploading ({len(data) // 1024} KB)...")
            dialog.update()
            
            payload = {
                "type": "UPLOAD_VERSION",
                "gameId": game["id"],
                "versionLabel": version_label,
                "clientEntrypoint": client_entry,
                "serverEntrypoint": server_entry,
                "clientMode": client_mode,
                "changelog": changelog_var.get().strip() or "",
                "package": base64.b64encode(data).decode("ascii"),
            }
            
            try:
                resp = self.conn.call(payload)
            except Exception as exc:
                status_var.set("")
                # Connection error - offer retry
                if messagebox.askretrycancel("Upload Failed", 
                    f"Connection error during upload:\n{exc}\n\n"
                    "The server may or may not have received the update.\n"
                    "You can check the game versions after reconnecting.\n\n"
                    "Retry upload?", 
                    parent=dialog):
                    submit()  # Retry
                return
            
            if resp.get("ok"):
                status_var.set("✅ Upload complete!")
                messagebox.showinfo("Version Uploaded", 
                    f"Version '{version_label}' uploaded successfully!\n\n"
                    f"Version ID: {resp['versionId']}\n\n"
                    f"This version is now the latest for '{game['title']}'.",
                    parent=dialog)
                dialog.destroy()
                self.refresh_games()
            else:
                status_var.set("")
                error_msg = resp.get("error", "Unknown error")
                messagebox.showerror("Upload Failed", f"Server rejected the upload:\n{error_msg}", parent=dialog)
        
        def cancel() -> None:
            dialog.destroy()
        
        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill="x", padx=10, pady=15)
        ttk.Button(btn_frame, text="Cancel", command=cancel).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📤 Upload Version", command=submit).pack(side=tk.RIGHT, padx=5)

    def update_status(self, status: str) -> None:
        """
        Update game status (publish/unpublish/retire)
        Implements Use Case D3 for retire operation: 開發者下架一款遊戲
        """
        game = self.get_selected_game()
        if not game:
            messagebox.showwarning("Update Status", "Please select a game from the list first.")
            return
        
        game_title = game.get("title", "Unknown")
        current_status = game.get("status", "draft").upper()
        
        # Handle publish
        if status == "published":
            if not game.get("latest_version_id"):
                messagebox.showwarning("Cannot Publish", 
                    f"Cannot publish '{game_title}'.\n\n"
                    "Please upload at least one version before publishing.")
                return
            
            if not messagebox.askyesno("Confirm Publish",
                f"Are you sure you want to publish '{game_title}'?\n\n"
                "Once published, this game will be visible in the store\n"
                "and players can download and play it."):
                return
        
        # Handle unpublish (set to draft)
        elif status == "draft":
            if current_status == "DRAFT":
                messagebox.showinfo("Status", f"'{game_title}' is already in draft status.")
                return
            
            if not messagebox.askyesno("Confirm Unpublish",
                f"Are you sure you want to unpublish '{game_title}'?\n\n"
                "Effects of unpublishing:\n"
                "• New players will NOT be able to see or download this game\n"
                "• New rooms CANNOT be created for this game\n"
                "• Players who already downloaded can still play locally\n"
                "• Existing game rooms may continue until finished\n\n"
                "You can re-publish the game later."):
                return
        
        # Handle retire (permanent removal from store)
        elif status == "retired":
            if current_status == "RETIRED":
                messagebox.showinfo("Status", f"'{game_title}' is already retired.")
                return
            
            # Show stronger warning for retire
            if not messagebox.askyesno("⚠️ Confirm Retire",
                f"Are you sure you want to RETIRE '{game_title}'?\n\n"
                "Effects of retiring:\n"
                "• This game will be PERMANENTLY removed from the store\n"
                "• New players will NOT be able to see or download this game\n"
                "• New rooms CANNOT be created for this game\n"
                "• Players who already downloaded can still play locally\n"
                "• Existing game rooms may continue until finished\n\n"
                "⚠️ This action is intended to be permanent.\n"
                "Consider using 'Unpublish' if you plan to re-release later.",
                icon="warning"):
                return
            
            # Double confirmation for retire
            if not messagebox.askyesno("Final Confirmation",
                f"This is your final confirmation.\n\n"
                f"Retire '{game_title}' permanently?",
                icon="warning"):
                return
        
        # Send request to server
        try:
            resp = self.conn.call({"type": "SET_STATUS", "gameId": game["id"], "status": status})
        except Exception as exc:
            # Connection error - state unchanged
            messagebox.showerror("Status Update Failed", 
                f"Failed to update game status due to connection error:\n{exc}\n\n"
                "The game status has NOT been changed.\n"
                "Please check your connection and try again.")
            return
        
        if resp.get("ok"):
            status_display = {
                "published": "Published ✅",
                "draft": "Unpublished (Draft)",
                "retired": "Retired 🚫"
            }.get(status, status)
            
            messagebox.showinfo("Status Updated", 
                f"'{game_title}' status updated to: {status_display}")
            self.refresh_games()
        else:
            error_msg = resp.get("error", "Unknown error")
            messagebox.showerror("Status Update Failed", 
                f"Server rejected the status change:\n{error_msg}\n\n"
                "The game status has NOT been changed.")

    def delete_game(self) -> None:
        """
        Delete a game and all its versions permanently.
        """
        game = self.get_selected_game()
        if not game:
            messagebox.showwarning("Delete Game", "Please select a game from the list first.")
            return
        
        game_title = game.get("title", "Unknown")
        
        # Show warning dialog
        if not messagebox.askyesno("⚠️ Confirm Delete",
            f"Are you sure you want to DELETE '{game_title}'?\n\n"
            "⚠️ This will PERMANENTLY delete:\n"
            "• The game and ALL its versions\n"
            "• All player reviews for this game\n"
            "• All stored game files\n\n"
            "This action CANNOT be undone!",
            icon="warning"):
            return
        
        # Double confirmation
        if not messagebox.askyesno("Final Confirmation",
            f"This is your FINAL confirmation.\n\n"
            f"Permanently delete '{game_title}' and all its data?",
            icon="warning"):
            return
        
        # Send delete request
        try:
            resp = self.conn.call({"type": "DELETE_GAME", "gameId": game["id"]})
        except Exception as exc:
            messagebox.showerror("Delete Failed", 
                f"Failed to delete game due to connection error:\n{exc}")
            return
        
        if resp.get("ok"):
            messagebox.showinfo("Game Deleted", 
                f"'{game_title}' has been permanently deleted.")
            self.refresh_games()
        else:
            error_msg = resp.get("error", "Unknown error")
            messagebox.showerror("Delete Failed", 
                f"Failed to delete game:\n{error_msg}")

    def upload_new_game_dialog(self) -> None:
        """
        One-click game upload dialog: Create game + Upload version + Publish
        Implements Use Case D1: 開發者上架一款新遊戲
        """
        if not self.developer:
            return
        
        # Step 1: Select game directory
        directory = filedialog.askdirectory(title="Select Game Directory to Upload")
        if not directory:
            return
        path = Path(directory)
        
        # Validate directory
        if not path.exists():
            messagebox.showerror("Directory Error", f"The selected directory does not exist:\n{path}")
            return
        if not path.is_dir():
            messagebox.showerror("Directory Error", f"The selected path is not a directory:\n{path}")
            return
        
        files_in_dir = list(path.rglob("*"))
        if not any(f.is_file() for f in files_in_dir):
            messagebox.showerror("Directory Error", "The selected directory is empty or contains no files.")
            return
        
        # Step 2: Read manifest.json if exists
        manifest_data: Dict[str, Any] = {}
        manifest_path = path / "manifest.json"
        manifest_warnings: List[str] = []
        
        if manifest_path.exists():
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest_data, dict):
                    manifest_warnings.append("manifest.json must contain a JSON object. Using manual input.")
                    manifest_data = {}
            except Exception as exc:
                manifest_warnings.append(f"Failed to parse manifest.json: {exc}")
                manifest_data = {}
        else:
            manifest_warnings.append("No manifest.json found. You'll need to fill in all fields manually.")
        
        # Validate manifest required fields
        required_manifest_fields = ["clientEntrypoint", "serverEntrypoint"]
        missing_fields = [f for f in required_manifest_fields if not manifest_data.get(f)]
        if missing_fields and manifest_path.exists():
            manifest_warnings.append(f"manifest.json is missing required fields: {', '.join(missing_fields)}")
        
        # Step 3: Show upload dialog with all options
        dialog = tk.Toplevel(self)
        dialog.title("📦 Upload New Game")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("500x650")
        
        # Show warnings if any
        if manifest_warnings:
            warn_frame = tk.Frame(dialog, bg="#fff3cd", padx=10, pady=5)
            warn_frame.pack(fill="x", padx=10, pady=5)
            tk.Label(warn_frame, text="⚠️ Warnings:", font=("Segoe UI", 9, "bold"), bg="#fff3cd", fg="#856404").pack(anchor="w")
            for warn in manifest_warnings:
                tk.Label(warn_frame, text=f"• {warn}", font=("Segoe UI", 8), bg="#fff3cd", fg="#856404", wraplength=450, justify="left").pack(anchor="w")
        
        # Game info section
        info_frame = ttk.LabelFrame(dialog, text="Game Information", padding=10)
        info_frame.pack(fill="x", padx=10, pady=5)
        
        title_var = tk.StringVar(value=manifest_data.get("name", "").replace("{{GAME_NAME}}", ""))
        summary_var = tk.StringVar(value=manifest_data.get("description", ""))
        category_var = tk.StringVar(value=manifest_data.get("category", "General"))
        min_players_var = tk.IntVar(value=manifest_data.get("minPlayers", 2))
        max_players_var = tk.IntVar(value=manifest_data.get("maxPlayers", 2))
        
        client_mode_from_manifest = str(manifest_data.get("clientMode", "gui")).lower()
        cli_var = tk.BooleanVar(value=client_mode_from_manifest == "cli")
        gui_var = tk.BooleanVar(value=client_mode_from_manifest == "gui")
        
        def add_row(parent: tk.Widget, row: int, label: str, widget: tk.Widget, required: bool = False) -> None:
            lbl_text = f"{label} *" if required else label
            ttk.Label(parent, text=lbl_text).grid(row=row, column=0, sticky="w", padx=4, pady=2)
            widget.grid(row=row, column=1, sticky="ew", padx=4, pady=2)
        
        add_row(info_frame, 0, "Title", ttk.Entry(info_frame, textvariable=title_var), required=True)
        add_row(info_frame, 1, "Description", ttk.Entry(info_frame, textvariable=summary_var))
        add_row(info_frame, 2, "Category", ttk.Entry(info_frame, textvariable=category_var))
        add_row(info_frame, 3, "Min Players", ttk.Spinbox(info_frame, from_=1, to=100, textvariable=min_players_var, width=10))
        add_row(info_frame, 4, "Max Players", ttk.Spinbox(info_frame, from_=1, to=100, textvariable=max_players_var, width=10))
        
        mode_frame = ttk.Frame(info_frame)
        ttk.Checkbutton(mode_frame, text="CLI", variable=cli_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(mode_frame, text="GUI", variable=gui_var).pack(side=tk.LEFT, padx=5)
        add_row(info_frame, 5, "Supports", mode_frame)
        
        info_frame.columnconfigure(1, weight=1)
        
        # Version info section
        ver_frame = ttk.LabelFrame(dialog, text="Version Information", padding=10)
        ver_frame.pack(fill="x", padx=10, pady=5)
        
        version_var = tk.StringVar(value=manifest_data.get("version", "1.0.0"))
        client_entry_var = tk.StringVar(value=manifest_data.get("clientEntrypoint", "python client.py"))
        server_entry_var = tk.StringVar(value=manifest_data.get("serverEntrypoint", "python server.py"))
        changelog_var = tk.StringVar(value=manifest_data.get("changelog", "") or manifest_data.get("description", ""))
        
        add_row(ver_frame, 0, "Version", ttk.Entry(ver_frame, textvariable=version_var), required=True)
        add_row(ver_frame, 1, "Client Entrypoint", ttk.Entry(ver_frame, textvariable=client_entry_var), required=True)
        add_row(ver_frame, 2, "Server Entrypoint", ttk.Entry(ver_frame, textvariable=server_entry_var), required=True)
        add_row(ver_frame, 3, "Changelog", ttk.Entry(ver_frame, textvariable=changelog_var))
        
        client_mode_var = tk.StringVar(value=client_mode_from_manifest)
        add_row(ver_frame, 4, "Client Mode", ttk.Combobox(ver_frame, textvariable=client_mode_var, values=["cli", "gui"], state="readonly"))
        
        ver_frame.columnconfigure(1, weight=1)
        
        # Publish option
        publish_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(dialog, text="Publish immediately after upload", variable=publish_var).pack(anchor="w", padx=15, pady=5)
        
        # Directory info
        dir_frame = ttk.Frame(dialog)
        dir_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(dir_frame, text=f"📁 Directory: {path}", font=("Segoe UI", 8), foreground="gray").pack(anchor="w")
        
        # Progress/status area
        status_var = tk.StringVar(value="")
        status_label = ttk.Label(dialog, textvariable=status_var, foreground="blue")
        status_label.pack(anchor="w", padx=15, pady=5)
        
        def submit() -> None:
            # Validate required fields
            title = title_var.get().strip()
            if not title:
                messagebox.showwarning("Validation Error", "Game title is required.", parent=dialog)
                return
            
            version = version_var.get().strip()
            if not version:
                messagebox.showwarning("Validation Error", "Version label is required.", parent=dialog)
                return
            
            client_entry = client_entry_var.get().strip()
            if not client_entry:
                messagebox.showwarning("Validation Error", "Client entrypoint is required.", parent=dialog)
                return
            
            server_entry = server_entry_var.get().strip()
            if not server_entry:
                messagebox.showwarning("Validation Error", "Server entrypoint is required.", parent=dialog)
                return
            
            min_p = min_players_var.get()
            max_p = max_players_var.get()
            if min_p < 1:
                messagebox.showwarning("Validation Error", "Min players must be at least 1.", parent=dialog)
                return
            if max_p < min_p:
                messagebox.showwarning("Validation Error", "Max players must be >= min players.", parent=dialog)
                return
            if not cli_var.get() and not gui_var.get():
                messagebox.showwarning("Validation Error", "Game must support at least CLI or GUI.", parent=dialog)
                return
            
            # Step 1: Create game
            status_var.set("Creating game...")
            dialog.update()
            
            try:
                create_resp = self.conn.call({
                    "type": "CREATE_GAME",
                    "title": title,
                    "summary": summary_var.get().strip(),
                    "category": category_var.get().strip() or "General",
                    "minPlayers": int(min_p),
                    "maxPlayers": int(max_p),
                    "supportCli": cli_var.get(),
                    "supportGui": gui_var.get(),
                })
            except Exception as exc:
                status_var.set("")
                if messagebox.askretrycancel("Connection Error", f"Failed to create game: {exc}\n\nRetry?", parent=dialog):
                    submit()  # Retry
                return
            
            if not create_resp.get("ok"):
                status_var.set("")
                messagebox.showerror("Create Game Failed", create_resp.get("error", "Unknown error"), parent=dialog)
                return
            
            game_id = create_resp["gameId"]
            status_var.set(f"Game created (ID: {game_id}). Packaging files...")
            dialog.update()
            
            # Step 2: Package directory
            try:
                data = zip_directory(path)
            except Exception as exc:
                status_var.set("")
                messagebox.showerror("Packaging Failed", f"Failed to package game files:\n{exc}", parent=dialog)
                return
            
            status_var.set(f"Uploading version ({len(data) // 1024} KB)...")
            dialog.update()
            
            # Step 3: Upload version
            try:
                upload_resp = self.conn.call({
                    "type": "UPLOAD_VERSION",
                    "gameId": game_id,
                    "versionLabel": version,
                    "clientEntrypoint": client_entry,
                    "serverEntrypoint": server_entry,
                    "clientMode": client_mode_var.get() or "gui",
                    "changelog": changelog_var.get().strip(),
                    "package": base64.b64encode(data).decode("ascii"),
                })
            except Exception as exc:
                status_var.set("")
                messagebox.showerror("Upload Failed", f"Failed to upload version: {exc}\n\nGame was created but no version uploaded.", parent=dialog)
                self.refresh_games()
                return
            
            if not upload_resp.get("ok"):
                status_var.set("")
                messagebox.showerror("Upload Failed", f"Upload error: {upload_resp.get('error', 'Unknown')}\n\nGame was created but no version uploaded.", parent=dialog)
                self.refresh_games()
                return
            
            # Step 4: Publish if requested
            if publish_var.get():
                status_var.set("Publishing game...")
                dialog.update()
                try:
                    pub_resp = self.conn.call({"type": "SET_STATUS", "gameId": game_id, "status": "published"})
                    if not pub_resp.get("ok"):
                        messagebox.showwarning("Publish Warning", f"Game uploaded but failed to publish: {pub_resp.get('error')}", parent=dialog)
                except Exception as exc:
                    messagebox.showwarning("Publish Warning", f"Game uploaded but failed to publish: {exc}", parent=dialog)
            
            # Success!
            status_var.set("✅ Upload complete!")
            messagebox.showinfo(
                "Upload Successful",
                f"Game '{title}' has been uploaded successfully!\n\n"
                f"Game ID: {game_id}\n"
                f"Version: {version}\n"
                f"Status: {'Published' if publish_var.get() else 'Draft'}",
                parent=dialog
            )
            dialog.destroy()
            self.refresh_games()
        
        def cancel() -> None:
            dialog.destroy()
        
        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill="x", padx=10, pady=15)
        ttk.Button(btn_frame, text="Cancel", command=cancel).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📤 Upload Game", command=submit).pack(side=tk.RIGHT, padx=5)


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
    app = DeveloperApp()
    app.mainloop()
