"""
開發者伺服器 (Developer Server)

處理開發者端的所有請求，是遊戲上架平台的核心服務

支援的操作:
- 帳號系統: REGISTER, LOGIN, LOGOUT
- Use Case D1 (上架新遊戲): CREATE_GAME + UPLOAD_VERSION
- Use Case D2 (更新版本): UPLOAD_VERSION
- Use Case D3 (下架遊戲): SET_STATUS -> retired
- 遊戲管理: LIST_GAMES, UPDATE_GAME, GET_GAME_REVIEWS

遊戲上架流程:
1. 開發者在本機開發遊戲 (game_templates/)
2. 使用 Developer Client 建立遊戲資訊 (CREATE_GAME)
3. 上傳遊戲套件 (UPLOAD_VERSION)
4. 發布遊戲 (SET_STATUS -> published)
5. 遊戲出現在玩家商城中

版本管理說明:
- 每個遊戲可以有多個版本
- latest_version_id 指向最新版本
- 玩家下載時會取得最新版本
- 舊版本仍可保留供已下載的玩家使用
"""
import argparse
import base64
import hashlib
import json
import os
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple

from common.lp import recv_json, send_json


# ============================================================
# DB Server 客戶端封裝
# ============================================================
class DBClient:
    """DB Server 客戶端，封裝資料庫操作"""
    
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    def call(self, entity: str, action: str, data: Dict) -> Dict:
        """向 DB Server 發送請求"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            send_json(s, {"entity": entity, "action": action, "data": data})
            resp = recv_json(s)
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error", "資料庫錯誤"))
            return resp["result"]


# ============================================================
# 開發者 Session 管理
# ============================================================
class DeveloperSession:
    """
    開發者登入 Session
    
    記錄開發者的登入狀態，包含:
    - account_id: 開發者帳號 ID
    - username: 使用者名稱
    - display_name: 顯示名稱
    - token: 唯一的 Session 識別碼
    
    用於避免重複登入與權限驗證
    """
    
    def __init__(self, account_id: int, username: str, display_name: str) -> None:
        self.account_id = account_id
        self.username = username
        self.display_name = display_name
        self.token = uuid.uuid4().hex
        self.issued_at = int(time.time())


# ============================================================
# 開發者伺服器主類別
# ============================================================
class DeveloperServer:
    """
    開發者伺服器主類別
    
    負責:
    1. 接受開發者連線並處理各種請求
    2. 與 DB Server 溝通存取資料
    3. 處理遊戲上架/更新/下架流程
    4. 儲存遊戲套件到 storage/games/ 目錄
    
    重要屬性:
    - sessions: 開發者連線 -> Session 的映射
    - storage_root: 遊戲檔案儲存路徑
    """
    
    def __init__(self, host: str, port: int, db_host: str, db_port: int, storage_root: Path) -> None:
        self.host = host
        self.port = port
        self.db = DBClient(db_host, db_port)
        self.storage_root = storage_root  # 遊戲檔案儲存路徑
        self.sessions: Dict[socket.socket, Optional[DeveloperSession]] = {}
        self.lock = threading.Lock()
        self.storage_root.mkdir(parents=True, exist_ok=True)

    def serve(self) -> None:
        """啟動伺服器"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen()
            print(f"[Developer Server] 啟動於 {self.host}:{self.port}")
            while True:
                conn, addr = s.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()

    def handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        """處理單一客戶端連線"""
        with self.lock:
            self.sessions[conn] = None
        try:
            while True:
                req = recv_json(conn)
                action = req.get("type")
                
                # 不需要登入的操作
                if action == "PING":
                    send_json(conn, {"ok": True, "pong": True})
                    continue
                if action == "REGISTER":
                    self.handle_register(conn, req)
                    continue
                if action == "LOGIN":
                    self.handle_login(conn, req)
                    continue
                    
                # 以下操作需要登入
                session = self.sessions.get(conn)
                if session is None:
                    send_json(conn, {"ok": False, "error": "請先登入"})
                    continue
                    
                if action == "LIST_GAMES":
                    # 列出開發者的所有遊戲
                    games = self.db.call("Game", "list_by_owner", {"ownerId": session.account_id})
                    versions = {}
                    for game in games:
                        gv = self.db.call("GameVersion", "list_by_game", {"gameId": game["id"]})
                        versions[game["id"]] = gv
                    send_json(conn, {"ok": True, "games": games, "versions": versions})
                    
                elif action == "GET_GAME_REVIEWS":
                    # 查看遊戲評論
                    game_id = req.get("gameId")
                    if not game_id:
                        send_json(conn, {"ok": False, "error": "請提供 gameId"})
                        continue
                    game = self.db.call("Game", "read", {"id": game_id})
                    if not game:
                        send_json(conn, {"ok": False, "error": "找不到遊戲"})
                        continue
                    if game.get("owner_id") != session.account_id:
                        send_json(conn, {"ok": False, "error": "無權限查看"})
                        continue
                    reviews = self.db.call("GameReview", "list_by_game", {"gameId": game_id})
                    send_json(conn, {"ok": True, "reviews": reviews})
                    
                elif action == "CREATE_GAME":
                    # Use Case D1: 建立新遊戲
                    self.handle_create_game(conn, session, req)
                    
                elif action == "UPDATE_GAME":
                    # 更新遊戲資訊
                    self.handle_update_game(conn, session, req)
                    
                elif action == "UPLOAD_VERSION":
                    # Use Case D2: 上傳新版本
                    self.handle_upload_version(conn, session, req)
                    
                elif action == "SET_STATUS":
                    # Use Case D3: 設定遊戲狀態 (published/retired)
                    self.handle_set_status(conn, session, req)
                
                elif action == "DELETE_GAME":
                    # 刪除遊戲
                    self.handle_delete_game(conn, session, req)
                    
                elif action == "LOGOUT":
                    with self.lock:
                        self.sessions[conn] = None
                    send_json(conn, {"ok": True})
                    
                else:
                    send_json(conn, {"ok": False, "error": f"未知的操作: {action}"})
                    
        except Exception as exc:
            print(f"[Developer Server] 連線錯誤 {addr}: {exc}")
        finally:
            with self.lock:
                self.sessions.pop(conn, None)
            conn.close()

    # ============================================================
    # 帳號相關 Handler
    # ============================================================
    def handle_register(self, conn: socket.socket, req: Dict) -> None:
        """處理開發者註冊"""
        username = req.get("username")
        password_hash = req.get("passwordHash")
        display_name = req.get("displayName") or username
        
        if not username or not password_hash:
            send_json(conn, {"ok": False, "error": "請提供帳號和密碼"})
            return
            
        # 檢查帳號是否已存在
        existing = self.db.call("DeveloperAccount", "read_by_username", {"username": username})
        if existing:
            send_json(conn, {"ok": False, "error": "帳號已被使用"})
            return
            
        new = self.db.call(
            "DeveloperAccount", "create",
            {"username": username, "passwordHash": password_hash, "displayName": display_name},
        )
        send_json(conn, {"ok": True, "developerId": new["id"]})

    def handle_login(self, conn: socket.socket, req: Dict) -> None:
        """處理開發者登入"""
        username = req.get("username")
        password_hash = req.get("passwordHash")
        
        if not username or not password_hash:
            send_json(conn, {"ok": False, "error": "請提供帳號和密碼"})
            return
            
        account = self.db.call("DeveloperAccount", "read_by_username", {"username": username})
        if not account:
            send_json(conn, {"ok": False, "error": "帳號或密碼錯誤"})
            return
        if account["password_hash"] != password_hash:
            send_json(conn, {"ok": False, "error": "帳號或密碼錯誤"})
            return
        
        # 檢查是否已經有其他連線登入此帳號（禁止重複登入）
        with self.lock:
            for existing_conn, existing_session in self.sessions.items():
                if existing_session and existing_session.account_id == account["id"]:
                    send_json(conn, {"ok": False, "error": "This account is already logged in from another session"})
                    return
            
        self.db.call("DeveloperAccount", "set_last_login", {"id": account["id"]})
        session = DeveloperSession(account["id"], account["username"], account["display_name"])
        
        with self.lock:
            self.sessions[conn] = session
            
        send_json(conn, {
            "ok": True,
            "token": session.token,
            "developer": {
                "id": session.account_id,
                "username": session.username,
                "displayName": session.display_name,
            },
        })

    # ============================================================
    # 遊戲管理 Handler (Use Case D1-D3)
    # ============================================================
    def handle_create_game(self, conn: socket.socket, session: DeveloperSession, req: Dict) -> None:
        """Use Case D1: 建立新遊戲"""
        title = req.get("title", "").strip()
        if not title:
            send_json(conn, {"ok": False, "error": "請輸入遊戲名稱"})
            return
        
        min_players = req.get("minPlayers", 2)
        max_players = req.get("maxPlayers", 2)
        if min_players < 1:
            send_json(conn, {"ok": False, "error": "最少玩家數必須 >= 1"})
            return
        if max_players < min_players:
            send_json(conn, {"ok": False, "error": "最大玩家數必須 >= 最少玩家數"})
            return
        
        payload = {
            "ownerId": session.account_id,
            "title": title,
            "summary": req.get("summary", ""),
            "category": req.get("category", "General"),
            "status": "draft",  # 新遊戲預設為草稿狀態
            "minPlayers": min_players,
            "maxPlayers": max_players,
            "supportCli": bool(req.get("supportCli", True)),
            "supportGui": bool(req.get("supportGui", True)),
        }
        result = self.db.call("Game", "create", payload)
        send_json(conn, {"ok": True, "gameId": result["id"]})

    def handle_update_game(self, conn: socket.socket, session: DeveloperSession, req: Dict) -> None:
        """更新遊戲資訊"""
        game_id = req.get("gameId")
        if not game_id:
            send_json(conn, {"ok": False, "error": "請提供 gameId"})
            return
        
        # 驗證擁有權
        game = self.db.call("Game", "read", {"id": game_id})
        if not game:
            send_json(conn, {"ok": False, "error": "找不到遊戲"})
            return
        if game.get("owner_id") != session.account_id:
            send_json(conn, {"ok": False, "error": "無權限更新此遊戲"})
            return
        
        data = {"id": game_id}
        for key in ["title", "summary", "category", "status", "minPlayers", "maxPlayers", 
                    "supportCli", "supportGui", "latestVersionId"]:
            if key in req:
                data[key] = req[key]
        self.db.call("Game", "update", data)
        send_json(conn, {"ok": True})

    def handle_set_status(self, conn: socket.socket, session: DeveloperSession, req: Dict) -> None:
        """Use Case D3: 設定遊戲狀態 (published=上架, retired=下架)"""
        game_id = req.get("gameId")
        if not game_id:
            send_json(conn, {"ok": False, "error": "請提供 gameId"})
            return
        
        game = self.db.call("Game", "read", {"id": game_id})
        if not game:
            send_json(conn, {"ok": False, "error": "找不到遊戲"})
            return
        if game.get("owner_id") != session.account_id:
            send_json(conn, {"ok": False, "error": "無權限修改此遊戲"})
            return
        
        new_status = req.get("status", "draft")
        self.db.call("Game", "update", {"id": game_id, "status": new_status})
        send_json(conn, {"ok": True})

    def handle_delete_game(self, conn: socket.socket, session: DeveloperSession, req: Dict) -> None:
        """刪除遊戲及其所有版本"""
        game_id = req.get("gameId")
        if not game_id:
            send_json(conn, {"ok": False, "error": "請提供 gameId"})
            return
        
        game = self.db.call("Game", "read", {"id": game_id})
        if not game:
            send_json(conn, {"ok": False, "error": "找不到遊戲"})
            return
        if game.get("owner_id") != session.account_id:
            send_json(conn, {"ok": False, "error": "無權限刪除此遊戲"})
            return
        
        # 刪除遊戲 (會連帶刪除版本、評論等)
        try:
            self.db.call("Game", "delete", {"id": game_id})
            
            # 刪除本地儲存的遊戲檔案
            game_folder = self.storage_root / f"game_{game_id}"
            if game_folder.exists():
                import shutil
                shutil.rmtree(game_folder, ignore_errors=True)
            
            send_json(conn, {"ok": True})
        except Exception as exc:
            send_json(conn, {"ok": False, "error": f"刪除失敗: {exc}"})

    def handle_upload_version(self, conn: socket.socket, session: DeveloperSession, req: Dict) -> None:
        """Use Case D1/D2: 上傳遊戲版本"""
        try:
            game_id = req["gameId"]
            version_label = req["versionLabel"]
            client_entry = req["clientEntrypoint"]
            server_entry = req["serverEntrypoint"]
            client_mode = req.get("clientMode", "gui").lower()
            package_b64 = req["package"]
        except KeyError as missing:
            send_json(conn, {"ok": False, "error": f"缺少必要欄位: {missing}"})
            return
        
        # 驗證版本標籤
        if not version_label or not version_label.strip():
            send_json(conn, {"ok": False, "error": "版本標籤不可為空"})
            return
        version_label = version_label.strip()
        
        # 驗證遊戲擁有權
        game = self.db.call("Game", "read", {"id": game_id})
        if not game:
            send_json(conn, {"ok": False, "error": "找不到遊戲"})
            return
        if game.get("owner_id") != session.account_id:
            send_json(conn, {"ok": False, "error": "無權限上傳此遊戲"})
            return
        
        # 檢查版本是否已存在
        existing_versions = self.db.call("GameVersion", "list_by_game", {"gameId": game_id})
        for v in existing_versions:
            if v.get("version_label") == version_label:
                send_json(conn, {"ok": False, "error": f"版本 '{version_label}' 已存在，請使用其他版本號"})
                return
        
        if client_mode not in {"cli", "gui"}:
            send_json(conn, {"ok": False, "error": "clientMode 必須是 cli 或 gui"})
            return
        
        # 解碼並儲存檔案
        raw = base64.b64decode(package_b64.encode("ascii"))
        if len(raw) == 0:
            send_json(conn, {"ok": False, "error": "上傳的檔案是空的"})
            return
            
        sha = hashlib.sha256(raw).hexdigest()
        folder = self.storage_root / f"game_{game_id}"
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{int(time.time())}_{version_label.replace('/', '_')}.zip"
        path = folder / filename
        
        with open(path, "wb") as fp:
            fp.write(raw)
        
        # 建立版本記錄
        record = self.db.call("GameVersion", "create", {
            "gameId": game_id,
            "versionLabel": version_label,
            "changelog": req.get("changelog", ""),
            "packagePath": str(path),
            "packageSize": len(raw),
            "packageSha256": sha,
            "clientEntrypoint": client_entry,
            "serverEntrypoint": server_entry,
            "clientMode": client_mode,
        })
        
        # 設為最新版本
        if req.get("makeLatest", True):
            self.db.call("Game", "update", {"id": game_id, "latestVersionId": record["id"]})
            
        send_json(conn, {"ok": True, "versionId": record["id"], "sha256": sha})


def main() -> None:
    """程式進入點"""
    parser = argparse.ArgumentParser(description="Developer Server - 處理開發者請求")
    parser.add_argument("--host", default="127.0.0.1", help="綁定的 IP 位址")
    parser.add_argument("--port", type=int, default=23001, help="綁定的 Port")
    parser.add_argument("--db-host", default="127.0.0.1", help="DB Server IP")
    parser.add_argument("--db-port", type=int, default=23000, help="DB Server Port")
    parser.add_argument("--storage", default="server/storage/games", help="遊戲檔案儲存路徑")
    args = parser.parse_args()

    storage_root = Path(args.storage)
    server = DeveloperServer(args.host, args.port, args.db_host, args.db_port, storage_root)
    server.serve()


if __name__ == "__main__":
    main()
