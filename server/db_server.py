"""
資料庫伺服器 (DB Server)

此伺服器負責所有資料的持久化儲存，提供 Entity-Action 風格的 API

支援的 Entity:
- DeveloperAccount: 開發者帳號 (註冊/登入/驗證)
- PlayerAccount: 玩家帳號 (註冊/登入/驗證)
- Game: 遊戲資訊 (上架/更新/下架)
- GameVersion: 遊戲版本 (上傳/版本管理)
- GameReview: 遊戲評論 (評分/留言)
- PlayerDownload: 玩家下載記錄
- Room: 遊戲房間 (建立/加入/啟動)
- RoomMember: 房間成員
- Plugin: 擴充功能套件 (加分項目)
- PlayerPlugin: 玩家已安裝的 Plugin
- Invite: 房間邀請
- RoomChat: 房間聊天記錄 (Plugin 功能)

API 格式:
請求: {"entity": "EntityName", "action": "actionName", "data": {...}}
回應: {"ok": true/false, "result": ..., "error": "..."}

範例:
    # 查詢玩家帳號
    {"entity": "PlayerAccount", "action": "read_by_username", "data": {"username": "alice"}}
    
    # 建立新遊戲
    {"entity": "Game", "action": "create", "data": {"title": "My Game", "ownerId": 1}}
    
    # 列出已上架遊戲
    {"entity": "Game", "action": "list_published", "data": {}}
"""
import argparse
import hashlib
import json
import sqlite3
import socket
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from common.lp import recv_json, send_json

# ============================================================
# 常數定義
# ============================================================
# Plugin 儲存路徑 (伺服器端)
PLUGINS_STORAGE = Path(__file__).parent / "storage" / "plugins"

# ============================================================
# 資料庫 Schema 定義
# 
# 使用 SQLite3 實作資料持久化
# 伺服器重啟後資料不會遺失 (符合作業要求)
# ============================================================
SCHEMA_STATEMENTS: List[str] = [
    # 開發者帳號表 (DeveloperAccount)
    # - 與玩家帳號分開管理 (符合作業要求)
    # - 用於開發者客戶端登入
    """
    CREATE TABLE IF NOT EXISTS developer_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        last_login_at INTEGER,
        locked INTEGER NOT NULL DEFAULT 0
    );
    """,
    # 玩家帳號表 (PlayerAccount)
    # - 與開發者帳號分開管理 (符合作業要求)
    # - 用於玩家客戶端登入
    """
    CREATE TABLE IF NOT EXISTS player_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        last_login_at INTEGER,
        locked INTEGER NOT NULL DEFAULT 0
    );
    """,
    # 遊戲表 (Game)
    # - status: draft=草稿, published=已上架, retired=已下架
    # - 用於 Use Case D1 (上架), D2 (更新), D3 (下架)
    """
    CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        category TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('draft','published','retired')),
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        min_players INTEGER NOT NULL DEFAULT 2,
        max_players INTEGER NOT NULL DEFAULT 2,
        support_cli INTEGER NOT NULL DEFAULT 1,
        support_gui INTEGER NOT NULL DEFAULT 1,
        latest_version_id INTEGER,
        FOREIGN KEY(owner_id) REFERENCES developer_accounts(id)
    );
    """,
    # 遊戲版本表 (GameVersion)
    # - 儲存遊戲的各個版本套件
    # - 支援版本更新與回滾 (Use Case D2)
    """
    CREATE TABLE IF NOT EXISTS game_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id INTEGER NOT NULL,
        version_label TEXT NOT NULL,
        changelog TEXT,
        package_path TEXT NOT NULL,
        package_size INTEGER NOT NULL,
        package_sha256 TEXT NOT NULL,
        client_entrypoint TEXT NOT NULL,
        server_entrypoint TEXT NOT NULL,
        client_mode TEXT NOT NULL DEFAULT 'gui',
        created_at INTEGER NOT NULL,
        FOREIGN KEY(game_id) REFERENCES games(id)
    );
    """,
    # 確保同一遊戲的版本標籤唯一
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ix_game_versions_unique ON game_versions(game_id, version_label);
    """,
    # 遊戲評論表 (GameReview)
    # - 每位玩家對每款遊戲只能有一則評論 (UNIQUE 約束)
    # - 支援 1.0-5.0 分的評分系統 (Use Case P4)
    """
    CREATE TABLE IF NOT EXISTS game_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        rating REAL NOT NULL CHECK(rating BETWEEN 1.0 AND 5.0),
        comment TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        UNIQUE(game_id, player_id),
        FOREIGN KEY(game_id) REFERENCES games(id),
        FOREIGN KEY(player_id) REFERENCES player_accounts(id)
    );
    """,
    # 玩家下載記錄表 (PlayerDownload)
    # - 追蹤玩家下載了哪些遊戲版本
    # - 用於判斷玩家是否有資格評論 (Use Case P4)
    """
    CREATE TABLE IF NOT EXISTS player_downloads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL,
        game_version_id INTEGER NOT NULL,
        downloaded_at INTEGER NOT NULL,
        UNIQUE(player_id, game_version_id),
        FOREIGN KEY(player_id) REFERENCES player_accounts(id),
        FOREIGN KEY(game_version_id) REFERENCES game_versions(id)
    );
    """,
    # 遊戲房間表 (Room)
    # - status: waiting=等待中, launching=啟動中, playing=遊戲中, closed=已關閉
    # - 存儲房間基本資訊與遊戲設定 (Use Case P3)
    """
    CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        owner_player_id INTEGER NOT NULL,
        game_id INTEGER NOT NULL,
        game_version_id INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('waiting','launching','playing','closed')),
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        capacity INTEGER NOT NULL,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY(owner_player_id) REFERENCES player_accounts(id),
        FOREIGN KEY(game_id) REFERENCES games(id),
        FOREIGN KEY(game_version_id) REFERENCES game_versions(id)
    );
    """,
    # 房間成員表 (RoomMember)
    # - 追蹤哪些玩家在哪些房間內
    # - 複合主鍵: (room_id, player_id)
    """
    CREATE TABLE IF NOT EXISTS room_members (
        room_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        joined_at INTEGER NOT NULL,
        PRIMARY KEY(room_id, player_id),
        FOREIGN KEY(room_id) REFERENCES rooms(id),
        FOREIGN KEY(player_id) REFERENCES player_accounts(id)
    );
    """,
    # Plugin 表 (加分功能 Use Case PL1-PL4)
    # - 存儲可用的擴充功能套件
    # - 每個 Plugin 有唯一的 slug 識別碼
    """
    CREATE TABLE IF NOT EXISTS plugins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        latest_version TEXT NOT NULL,
        package_path TEXT NOT NULL,
        package_size INTEGER NOT NULL,
        package_sha256 TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    """,
    # 玩家已安裝的 Plugin 表
    """
    CREATE TABLE IF NOT EXISTS player_plugins (
        player_id INTEGER NOT NULL,
        plugin_id INTEGER NOT NULL,
        installed_version TEXT NOT NULL,
        installed_at INTEGER NOT NULL,
        PRIMARY KEY(player_id, plugin_id),
        FOREIGN KEY(player_id) REFERENCES player_accounts(id),
        FOREIGN KEY(plugin_id) REFERENCES plugins(id)
    );
    """,
    # 房間邀請表
    """
    CREATE TABLE IF NOT EXISTS invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id INTEGER NOT NULL,
        from_player_id INTEGER NOT NULL,
        to_player_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined')),
        created_at INTEGER NOT NULL,
        FOREIGN KEY(room_id) REFERENCES rooms(id),
        FOREIGN KEY(from_player_id) REFERENCES player_accounts(id),
        FOREIGN KEY(to_player_id) REFERENCES player_accounts(id)
    );
    """,
    # 房間聊天記錄表 (Plugin 功能)
    """
    CREATE TABLE IF NOT EXISTS room_chat (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        FOREIGN KEY(room_id) REFERENCES rooms(id),
        FOREIGN KEY(player_id) REFERENCES player_accounts(id)
    );
    """
]


# ============================================================
# SQLite 適配器
# ============================================================
class SQLiteAdapter:
    """簡易 SQLite 封裝，提供基本的資料庫操作"""
    
    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # 初始化所有 Schema
        cur = self.conn.cursor()
        for stmt in SCHEMA_STATEMENTS:
            cur.executescript(stmt)
        self.conn.commit()

    def row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """將 Row 物件轉換為 dict"""
        return {k: row[k] for k in row.keys()}

    def exec(self, sql: str, params: Tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """執行 SQL 語句"""
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self) -> None:
        """提交交易"""
        self.conn.commit()


# ============================================================
# DB Server 主類別
# ============================================================
class DBServer:
    """
    資料庫伺服器，接收 JSON 請求並分發到對應的 handler
    請求格式: {"entity": "EntityName", "action": "actionName", "data": {...}}
    回應格式: {"ok": true/false, "result": ..., "error": "..."}
    """
    
    def __init__(self, host: str, port: int, db_path: Path) -> None:
        self.host = host
        self.port = port
        self.db = SQLiteAdapter(db_path)

    def serve(self) -> None:
        """啟動伺服器，監聽連線"""
        # 啟動時自動註冊所有內建 Plugins
        self._auto_register_plugins()
        
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen()
            print(f"[DB Server] 啟動於 {self.host}:{self.port}")
            while True:
                conn, addr = s.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()

    def _auto_register_plugins(self) -> None:
        """自動掃描並註冊 storage/plugins/ 目錄下的所有 Plugin"""
        if not PLUGINS_STORAGE.exists():
            print("[DB Server] Plugins 目錄不存在，跳過自動註冊")
            return
        
        plugin_dirs = [d for d in PLUGINS_STORAGE.iterdir() if d.is_dir()]
        if not plugin_dirs:
            print("[DB Server] 沒有找到任何 Plugin")
            return
        
        print(f"[DB Server] 自動註冊 {len(plugin_dirs)} 個 Plugin...")
        
        for plugin_dir in plugin_dirs:
            try:
                plugin_info = self._package_plugin(plugin_dir)
                if plugin_info:
                    self._register_plugin(plugin_info)
                    print(f"  ✅ {plugin_info['name']} v{plugin_info['version']}")
            except Exception as e:
                print(f"  ❌ {plugin_dir.name}: {e}")
    
    def _package_plugin(self, plugin_dir: Path) -> Dict[str, Any] | None:
        """打包 Plugin 目錄為 zip 並返回 metadata"""
        plugin_json = plugin_dir / "plugin.json"
        if not plugin_json.exists():
            return None
        
        metadata = json.loads(plugin_json.read_text(encoding="utf-8"))
        
        # 創建 zip 包
        zip_path = plugin_dir / f"{metadata['slug']}.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in plugin_dir.iterdir():
                if file.suffix == '.zip':
                    continue
                if file.is_file():
                    zf.write(file, file.name)
        
        # 計算 hash
        zip_data = zip_path.read_bytes()
        sha256 = hashlib.sha256(zip_data).hexdigest()
        
        return {
            **metadata,
            "package_path": str(zip_path.resolve()),
            "package_size": len(zip_data),
            "package_sha256": sha256,
        }
    
    def _register_plugin(self, plugin_info: Dict[str, Any]) -> None:
        """將 Plugin 資訊寫入資料庫"""
        now = self.now()
        self.db.exec(
            """
            INSERT INTO plugins(slug, name, description, latest_version, package_path, package_size, package_sha256, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                latest_version=excluded.latest_version,
                package_path=excluded.package_path,
                package_size=excluded.package_size,
                package_sha256=excluded.package_sha256,
                updated_at=excluded.updated_at
            """,
            (
                plugin_info["slug"],
                plugin_info["name"],
                plugin_info.get("description", ""),
                plugin_info["version"],
                plugin_info["package_path"],
                plugin_info["package_size"],
                plugin_info["package_sha256"],
                now,
                now,
            ),
        )
        self.db.commit()

    def handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        """處理單一客戶端連線，動態分發請求到對應的 handler"""
        try:
            while True:
                req = recv_json(conn)
                entity = req.get("entity")
                action = req.get("action")
                payload = req.get("data") or {}
                
                if not entity or not action:
                    send_json(conn, {"ok": False, "error": "缺少 entity 或 action"})
                    continue
                
                # 顯示關鍵操作日誌
                if entity in ("PlayerAccount", "DeveloperAccount") and action in ("create", "set_last_login"):
                    username = payload.get("username", payload.get("id", "?"))
                    print(f"[DB] {entity}.{action} -> {username}")
                elif entity in ("Room", "RoomMember", "Invite", "Game", "GameVersion"):
                    print(f"[DB] {entity}.{action} -> {payload}")
                    
                try:
                    # 動態取得 handler (例如 handle_game, handle_room 等)
                    handler = getattr(self, f"handle_{entity.lower()}")
                except AttributeError:
                    send_json(conn, {"ok": False, "error": f"未知的 entity: {entity}"})
                    continue
                    
                try:
                    result = handler(action, payload)
                    send_json(conn, {"ok": True, "result": result})
                except Exception as exc:
                    send_json(conn, {"ok": False, "error": str(exc)})
        except Exception:
            pass
        finally:
            conn.close()

    # ============================================================
    # 工具方法
    # ============================================================
    def now(self) -> int:
        """取得目前 Unix timestamp"""
        return int(time.time())

    def fetch_one_dict(self, sql: str, params: Tuple[Any, ...]) -> Dict[str, Any]:
        """查詢單一筆資料，回傳 dict 或 None"""
        cur = self.db.exec(sql, params)
        row = cur.fetchone()
        return self.db.row_to_dict(row) if row else None

    def fetch_all_dicts(self, sql: str, params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
        """查詢多筆資料，回傳 list of dict"""
        cur = self.db.exec(sql, params)
        rows = cur.fetchall()
        return [self.db.row_to_dict(r) for r in rows]

    # ============================================================
    # 開發者帳號 Entity Handler
    # ============================================================
    def handle_developeraccount(self, action: str, data: Dict[str, Any]) -> Any:
        """處理開發者帳號相關操作"""
        if action == "create":
            # 註冊新開發者帳號
            now = self.now()
            cur = self.db.exec(
                "INSERT INTO developer_accounts(username, display_name, password_hash, created_at) VALUES(?,?,?,?)",
                (data["username"], data.get("displayName") or data["username"], data["passwordHash"], now),
            )
            self.db.commit()
            return {"id": cur.lastrowid}
            
        if action == "read_by_username":
            # 根據使用者名稱查詢
            return self.fetch_one_dict("SELECT * FROM developer_accounts WHERE username=?", (data["username"],))
            
        if action == "read":
            # 根據 ID 查詢
            return self.fetch_one_dict("SELECT * FROM developer_accounts WHERE id=?", (data["id"],))
            
        if action == "set_last_login":
            # 更新最後登入時間
            self.db.exec("UPDATE developer_accounts SET last_login_at=? WHERE id=?", (self.now(), data["id"]))
            self.db.commit()
            return {"ok": True}
            
        raise ValueError(f"不支援的開發者帳號操作: {action}")

    # ============================================================
    # 玩家帳號 Entity Handler
    # ============================================================
    def handle_playeraccount(self, action: str, data: Dict[str, Any]) -> Any:
        """處理玩家帳號相關操作"""
        if action == "create":
            now = self.now()
            cur = self.db.exec(
                "INSERT INTO player_accounts(username, display_name, password_hash, created_at) VALUES(?,?,?,?)",
                (data["username"], data.get("displayName") or data["username"], data["passwordHash"], now),
            )
            self.db.commit()
            return {"id": cur.lastrowid}
            
        if action == "read_by_username":
            return self.fetch_one_dict("SELECT * FROM player_accounts WHERE username=?", (data["username"],))
            
        if action == "read":
            return self.fetch_one_dict("SELECT * FROM player_accounts WHERE id=?", (data["id"],))
            
        if action == "set_last_login":
            self.db.exec("UPDATE player_accounts SET last_login_at=? WHERE id=?", (self.now(), data["id"]))
            self.db.commit()
            return {"ok": True}
            
        raise ValueError(f"不支援的玩家帳號操作: {action}")

    # ============================================================
    # 遊戲 Entity Handler (Use Case D1-D3)
    # ============================================================
    def handle_game(self, action: str, data: Dict[str, Any]) -> Any:
        """處理遊戲相關操作 - 用於開發者上架/更新/下架遊戲"""
        if action == "create":
            # D1: 建立新遊戲
            now = self.now()
            cur = self.db.exec(
                """
                INSERT INTO games(owner_id, title, summary, category, status, created_at, updated_at, min_players, max_players, support_cli, support_gui)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    data["ownerId"],
                    data["title"],
                    data.get("summary", ""),
                    data.get("category", "General"),
                    data.get("status", "draft"),
                    now,
                    now,
                    data.get("minPlayers", 2),
                    data.get("maxPlayers", 2),
                    1 if data.get("supportCli", True) else 0,
                    1 if data.get("supportGui", True) else 0,
                ),
            )
            self.db.commit()
            return {"id": cur.lastrowid}
        if action == "update":
            fields = []
            values: List[Any] = []
            mapping = {
                "title": "title",
                "summary": "summary",
                "category": "category",
                "status": "status",
                "minPlayers": "min_players",
                "maxPlayers": "max_players",
                "supportCli": "support_cli",
                "supportGui": "support_gui",
                "latestVersionId": "latest_version_id",
            }
            for key, column in mapping.items():
                if key in data:
                    values.append(data[key])
                    fields.append(f"{column}=?")
            if not fields:
                return {"updated": 0}
            values.append(self.now())
            fields.append("updated_at=?")
            values.append(data["id"])
            sql = f"UPDATE games SET {', '.join(fields)} WHERE id=?"
            cur = self.db.exec(sql, tuple(values))
            self.db.commit()
            return {"updated": cur.rowcount}
        if action == "delete":
            # 刪除遊戲及其相關資料
            game_id = data["id"]
            # 先刪除相關資料 (外鍵約束)
            self.db.exec("DELETE FROM game_reviews WHERE game_id=?", (game_id,))
            self.db.exec("DELETE FROM game_versions WHERE game_id=?", (game_id,))
            self.db.exec("DELETE FROM rooms WHERE game_id=?", (game_id,))
            # 最後刪除遊戲本身
            cur = self.db.exec("DELETE FROM games WHERE id=?", (game_id,))
            self.db.commit()
            return {"deleted": cur.rowcount}
        if action == "read":
            return self.fetch_one_dict("SELECT * FROM games WHERE id=?", (data["id"],))
        if action == "list_by_owner":
            return self.fetch_all_dicts(
                "SELECT * FROM games WHERE owner_id=? ORDER BY created_at DESC",
                (data["ownerId"],),
            )
        if action == "list_published":
            # JOIN game_versions 來獲取 latest_version_label
            return self.fetch_all_dicts(
                """
                SELECT g.*, 
                       IFNULL(avg(r.rating), 0) AS avg_rating, 
                       COUNT(DISTINCT r.id) AS review_count,
                       v.version_label AS latest_version_label
                FROM games g
                LEFT JOIN game_reviews r ON g.id = r.game_id
                LEFT JOIN game_versions v ON g.latest_version_id = v.id
                WHERE g.status='published'
                GROUP BY g.id
                ORDER BY g.updated_at DESC
                """,
                (),
            )
        raise ValueError("unsupported game action")

    # Game Versions
    def handle_gameversion(self, action: str, data: Dict[str, Any]) -> Any:
        if action == "create":
            now = self.now()
            # First try to get existing version
            existing = self.fetch_one_dict(
                "SELECT id FROM game_versions WHERE game_id=? AND version_label=?",
                (data["gameId"], data["versionLabel"])
            )
            
            if existing:
                # Update existing version
                self.db.exec(
                    """
                    UPDATE game_versions 
                    SET changelog=?, package_path=?, package_size=?, package_sha256=?, 
                        client_entrypoint=?, server_entrypoint=?, client_mode=?, created_at=?
                    WHERE id=?
                    """,
                    (
                        data.get("changelog", ""),
                        data["packagePath"],
                        data["packageSize"],
                        data["packageSha256"],
                        data["clientEntrypoint"],
                        data["serverEntrypoint"],
                        data.get("clientMode", "gui"),
                        now,
                        existing["id"],
                    ),
                )
                self.db.commit()
                return {"id": existing["id"]}
            else:
                # Insert new version
                cur = self.db.exec(
                    """
                    INSERT INTO game_versions(game_id, version_label, changelog, package_path, package_size, package_sha256, client_entrypoint, server_entrypoint, client_mode, created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        data["gameId"],
                        data["versionLabel"],
                        data.get("changelog", ""),
                        data["packagePath"],
                        data["packageSize"],
                        data["packageSha256"],
                        data["clientEntrypoint"],
                        data["serverEntrypoint"],
                        data.get("clientMode", "gui"),
                        now,
                    ),
                )
                self.db.commit()
                return {"id": cur.lastrowid}
        if action == "read":
            return self.fetch_one_dict("SELECT * FROM game_versions WHERE id=?", (data["id"],))
        if action == "list_by_game":
            return self.fetch_all_dicts(
                "SELECT * FROM game_versions WHERE game_id=? ORDER BY created_at DESC",
                (data["gameId"],),
            )
        raise ValueError("unsupported game version action")

    # Reviews
    def handle_gamereview(self, action: str, data: Dict[str, Any]) -> Any:
        if action == "upsert":
            now = self.now()
            self.db.exec(
                """
                INSERT INTO game_reviews(game_id, player_id, rating, comment, created_at, updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(game_id, player_id) DO UPDATE SET
                    rating=excluded.rating,
                    comment=excluded.comment,
                    updated_at=excluded.updated_at
                """,
                (
                    data["gameId"],
                    data["playerId"],
                    data["rating"],
                    data.get("comment", ""),
                    now,
                    now,
                ),
            )
            self.db.commit()
            return {"ok": True}
        if action == "list_by_game":
            return self.fetch_all_dicts(
                """
                SELECT r.*, p.display_name AS player_name
                FROM game_reviews r
                JOIN player_accounts p ON r.player_id = p.id
                WHERE r.game_id=?
                ORDER BY r.updated_at DESC
                """,
                (data["gameId"],),
            )
        raise ValueError("unsupported review action")

    # Downloads
    def handle_playerdownload(self, action: str, data: Dict[str, Any]) -> Any:
        if action == "record":
            now = self.now()
            self.db.exec(
                """
                INSERT INTO player_downloads(player_id, game_version_id, downloaded_at)
                VALUES(?,?,?)
                ON CONFLICT(player_id, game_version_id) DO UPDATE SET downloaded_at=excluded.downloaded_at
                """,
                (data["playerId"], data["gameVersionId"], now),
            )
            self.db.commit()
            return {"ok": True}
        if action == "list_versions":
            return self.fetch_all_dicts(
                "SELECT * FROM player_downloads WHERE player_id=?",
                (data["playerId"],),
            )
        raise ValueError("unsupported download action")

    # Rooms
    def handle_room(self, action: str, data: Dict[str, Any]) -> Any:
        if action == "create":
            now = self.now()
            cur = self.db.exec(
                """
                INSERT INTO rooms(code, owner_player_id, game_id, game_version_id, status, created_at, updated_at, capacity, metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    data["code"],
                    data["ownerPlayerId"],
                    data["gameId"],
                    data["gameVersionId"],
                    data.get("status", "waiting"),
                    now,
                    now,
                    data.get("capacity", 4),
                    data.get("metadataJson", "{}"),
                ),
            )
            self.db.commit()
            return {"id": cur.lastrowid}
        if action == "update_status":
            cur = self.db.exec(
                "UPDATE rooms SET status=?, updated_at=? WHERE id=?",
                (data["status"], self.now(), data["id"]),
            )
            self.db.commit()
            return {"updated": cur.rowcount}
        if action == "delete":
            cur = self.db.exec(
                "DELETE FROM rooms WHERE id=?",
                (data["id"],),
            )
            self.db.commit()
            return {"deleted": cur.rowcount}
        if action == "read_by_code":
            return self.fetch_one_dict(
                "SELECT * FROM rooms WHERE code=?",
                (data["code"],),
            )
        if action == "read":
            return self.fetch_one_dict("SELECT * FROM rooms WHERE id=?", (data["id"],))
        if action == "list_open":
            return self.fetch_all_dicts(
                "SELECT * FROM rooms WHERE status IN ('waiting','launching','playing') ORDER BY updated_at DESC",
                (),
            )
        if action == "list_by_owner":
            return self.fetch_all_dicts(
                "SELECT * FROM rooms WHERE owner_player_id=? AND status IN ('waiting','launching','playing')",
                (data["ownerId"],),
            )
        raise ValueError("unsupported room action")

    # Room Members
    def handle_roommember(self, action: str, data: Dict[str, Any]) -> Any:
        if action == "add":
            self.db.exec(
                "INSERT OR IGNORE INTO room_members(room_id, player_id, joined_at) VALUES(?,?,?)",
                (data["roomId"], data["playerId"], self.now()),
            )
            self.db.commit()
            return {"ok": True}
        if action == "remove":
            cur = self.db.exec(
                "DELETE FROM room_members WHERE room_id=? AND player_id=?",
                (data["roomId"], data["playerId"]),
            )
            self.db.commit()
            return {"deleted": cur.rowcount}
        if action == "list":
            return self.fetch_all_dicts(
                """
                SELECT rm.room_id, rm.player_id, rm.joined_at, p.display_name, p.username
                FROM room_members rm
                JOIN player_accounts p ON rm.player_id = p.id
                WHERE rm.room_id=?
                ORDER BY rm.joined_at ASC
                """,
                (data["roomId"],),
            )
        if action == "clear_room":
            self.db.exec(
                "DELETE FROM room_members WHERE room_id=?",
                (data["roomId"],),
            )
            self.db.commit()
            return {"ok": True}
        if action == "delete_by_player":
            cur = self.db.exec(
                "DELETE FROM room_members WHERE player_id=?",
                (data["playerId"],),
            )
            self.db.commit()
            return {"deleted": cur.rowcount}
        if action == "find_player_room":
            # Find if player is in any open room
            return self.fetch_one_dict(
                """
                SELECT rm.room_id, r.code, r.status
                FROM room_members rm
                JOIN rooms r ON rm.room_id = r.id
                WHERE rm.player_id=? AND r.status IN ('waiting','launching','playing')
                LIMIT 1
                """,
                (data["playerId"],),
            )
        if action == "list_by_player":
            # List all rooms the player is in
            return self.fetch_all_dicts(
                """
                SELECT rm.room_id, rm.joined_at, r.code, r.status
                FROM room_members rm
                JOIN rooms r ON rm.room_id = r.id
                WHERE rm.player_id=? AND r.status IN ('waiting','launching','playing')
                """,
                (data["playerId"],),
            )
        raise ValueError("unsupported room member action")

    # Plugins
    def handle_plugin(self, action: str, data: Dict[str, Any]) -> Any:
        if action == "upsert":
            now = self.now()
            self.db.exec(
                """
                INSERT INTO plugins(slug, name, description, latest_version, package_path, package_size, package_sha256, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(slug) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    latest_version=excluded.latest_version,
                    package_path=excluded.package_path,
                    package_size=excluded.package_size,
                    package_sha256=excluded.package_sha256,
                    updated_at=excluded.updated_at
                """,
                (
                    data["slug"],
                    data["name"],
                    data.get("description", ""),
                    data["latestVersion"],
                    data["packagePath"],
                    data["packageSize"],
                    data["packageSha256"],
                    now,
                    now,
                ),
            )
            self.db.commit()
            return {"ok": True}
        if action == "list":
            return self.fetch_all_dicts("SELECT * FROM plugins ORDER BY updated_at DESC", ())
        if action == "read":
            return self.fetch_one_dict("SELECT * FROM plugins WHERE slug=?", (data["slug"],))
        raise ValueError("unsupported plugin action")

    # Player Plugins
    def handle_playerplugin(self, action: str, data: Dict[str, Any]) -> Any:
        if action == "install":
            now = self.now()
            self.db.exec(
                """
                INSERT INTO player_plugins(player_id, plugin_id, installed_version, installed_at)
                VALUES(?,?,?,?)
                ON CONFLICT(player_id, plugin_id) DO UPDATE SET
                    installed_version=excluded.installed_version,
                    installed_at=excluded.installed_at
                """,
                (
                    data["playerId"],
                    data["pluginId"],
                    data["version"],
                    now,
                ),
            )
            self.db.commit()
            return {"ok": True}
        if action == "remove":
            cur = self.db.exec(
                "DELETE FROM player_plugins WHERE player_id=? AND plugin_id=?",
                (data["playerId"], data["pluginId"]),
            )
            self.db.commit()
            return {"deleted": cur.rowcount}
        if action == "list_by_player":
            return self.fetch_all_dicts(
                """
                SELECT pp.*, pl.slug, pl.name
                FROM player_plugins pp
                JOIN plugins pl ON pp.plugin_id = pl.id
                WHERE pp.player_id=?
                ORDER BY pl.name ASC
                """,
                (data["playerId"],),
            )
        raise ValueError("unsupported player plugin action")

    # Invites
    def handle_invite(self, action: str, data: Dict[str, Any]) -> Any:
        if action == "create":
            now = self.now()
            cur = self.db.exec(
                "INSERT INTO invites(room_id, from_player_id, to_player_id, status, created_at) VALUES(?,?,?,?,?)",
                (data["roomId"], data["fromPlayerId"], data["toPlayerId"], "pending", now),
            )
            self.db.commit()
            return {"id": cur.lastrowid}
        if action == "read":
            return self.fetch_one_dict("SELECT * FROM invites WHERE id=?", (data["id"],))
        if action == "list_by_player":
            return self.fetch_all_dicts(
                """
                SELECT i.*, r.code as room_code, g.title as game_title,
                       fp.username as from_username, fp.display_name as from_display_name
                FROM invites i
                JOIN rooms r ON i.room_id = r.id
                JOIN games g ON r.game_id = g.id
                JOIN player_accounts fp ON i.from_player_id = fp.id
                WHERE i.to_player_id=? AND i.status='pending'
                ORDER BY i.created_at DESC
                """,
                (data["playerId"],),
            )
        if action == "update_status":
            self.db.exec(
                "UPDATE invites SET status=? WHERE id=?",
                (data["status"], data["id"]),
            )
            self.db.commit()
            return {"ok": True}
        if action == "delete_by_room":
            self.db.exec("DELETE FROM invites WHERE room_id=?", (data["roomId"],))
            self.db.commit()
            return {"ok": True}
        if action == "delete_by_player":
            self.db.exec(
                "DELETE FROM invites WHERE from_player_id=? OR to_player_id=?",
                (data["playerId"], data["playerId"]),
            )
            self.db.commit()
            return {"ok": True}
        raise ValueError("unsupported invite action")

    # Room Chat (Plugin)
    def handle_roomchat(self, action: str, data: Dict[str, Any]) -> Any:
        if action == "create":
            now = self.now()
            cur = self.db.exec(
                "INSERT INTO room_chat(room_id, player_id, message, created_at) VALUES(?,?,?,?)",
                (data["roomId"], data["playerId"], data["message"], now),
            )
            self.db.commit()
            return {"id": cur.lastrowid}
        if action == "list":
            limit = data.get("limit", 50)
            return self.fetch_all_dicts(
                """
                SELECT rc.*, pa.username, pa.display_name
                FROM room_chat rc
                JOIN player_accounts pa ON rc.player_id = pa.id
                WHERE rc.room_id=?
                ORDER BY rc.created_at DESC
                LIMIT ?
                """,
                (data["roomId"], limit),
            )
        if action == "delete_by_room":
            self.db.exec("DELETE FROM room_chat WHERE room_id=?", (data["roomId"],))
            self.db.commit()
            return {"ok": True}
        raise ValueError("unsupported room chat action")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=23000)
    parser.add_argument("--db", default="store.sqlite3")
    args = parser.parse_args()

    db_path = Path(args.db)
    server = DBServer(args.host, args.port, db_path)
    server.serve()


if __name__ == "__main__":
    main()
