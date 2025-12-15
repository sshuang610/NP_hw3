"""
大廳伺服器 (Lobby Server)

處理玩家端的所有請求，是遊戲大廳的核心服務

支援的操作:
- 帳號系統: REGISTER, LOGIN, LOGOUT
- Use Case P1 (瀏覽商城): LIST_GAMES, GET_GAME_DETAILS
- Use Case P2 (下載/更新): DOWNLOAD_GAME
- Use Case P3 (房間系統): CREATE_ROOM, JOIN_ROOM, LEAVE_ROOM, START_GAME
- Use Case P4 (評論系統): SUBMIT_REVIEW
- Use Case PL1-PL4 (Plugin): PLUGIN_LIST, PLUGIN_INSTALL, PLUGIN_REMOVE
- 社交功能: INVITE, LIST_INVITES, ACCEPT_INVITE, LIST_ACTIVE_PLAYERS
- Plugin 聊天: ROOM_CHAT, GET_ROOM_CHAT_HISTORY

架構說明:
- 通過 DBClient 與 DB Server 溝通
- 維護玄家 Session 與活躍房間資訊
- 負責遊戲 Server 的啟動與管理

遊戲啟動流程:
1. 玩家選擇遊戲並建立房間
2. 其他玩家加入房間
3. 房主點擊 Start Game
4. Lobby Server 啟動對應的 Game Server
5. 玩家端收到連線資訊，啟動 Game Client
6. 遊戲結束後，Lobby Server 自動清理資源
"""
import argparse
import base64
import hashlib
import json
import os
import random
import shutil
import socket
import string
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.lp import find_free_port, recv_json, send_json

# ============================================================
# 常數定義
# ============================================================
# 房間代碼使用的字元集 (A-Z, 0-9)
CODE_ALPHABET = string.ascii_uppercase + string.digits


# ============================================================
# DB Server 客戶端封裝
# ============================================================
class DBClient:
    """
    DB Server 客戶端
    
    封裝與 DB Server 的通訊，提供簡單的 call() 方法
    每次呼叫都建立新的 TCP 連線 (簡化設計)
    
    使用範例:
        db = DBClient("127.0.0.1", 23000)
        result = db.call("Game", "list_published", {})
    """
    
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
# 玩家 Session 管理
# ============================================================
class PlayerSession:
    """
    玩家登入 Session
    
    記錄玩家的登入狀態，包含:
    - account: 玩家帳號資訊 (從 DB 取得)
    - token: 唯一的 Session 識別碼
    - logged_in_at: 登入時間
    
    用於避免重複登入與追蹤線上玩家
    """
    
    def __init__(self, account: Dict[str, any]) -> None:
        self.account = account
        self.token = f"ps_{account['id']}_{int(time.time())}"
        self.logged_in_at = int(time.time())


# ============================================================
# 大廳伺服器主類別
# ============================================================
class LobbyServer:
    """
    大廳伺服器主類別
    
    負責:
    1. 接受玩家連線並處理各種請求
    2. 與 DB Server 溝通存取資料
    3. 管理遊戲房間的生命週期
    4. 啟動/停止 Game Server 程序
    
    重要屬性:
    - sessions: 玩家連線 -> Session 的映射
    - active_rooms: 活躍房間的資訊 (包含 Game Server process)
    - runtime_root: 遊戲執行時的暫存目錄
    """
    
    def __init__(self, host: str, port: int, db_host: str, db_port: int, public_host: Optional[str] = None) -> None:
        self.host = host
        self.port = port
        self.public_host = public_host or host  # 用於告知客戶端連線的位址
        self.db = DBClient(db_host, db_port)
        self.sessions: Dict[socket.socket, Optional[PlayerSession]] = {}
        self.active_rooms: Dict[int, Dict[str, any]] = {}  # 追蹤活躍房間
        self.lock = threading.Lock()
        self.runtime_root = Path(__file__).resolve().parent / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)

    # ============================================================
    # 房間清理相關方法
    # ============================================================
    def cleanup_room(self, room_id: int) -> None:
        """清理房間：刪除邀請、成員、房間本身"""
        with self.lock:
            active = self.active_rooms.pop(room_id, None)
        if active:
            # 終止遊戲 Server Process
            process = active.get("process")
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    pass
            # 清理 Runtime 資料夾
            runtime = active.get("runtime")
            if runtime:
                try:
                    shutil.rmtree(runtime, ignore_errors=True)
                except Exception:
                    pass
        # 清理資料庫記錄
        try:
            self.db.call("Invite", "delete_by_room", {"roomId": room_id})
        except Exception:
            pass
        try:
            self.db.call("RoomMember", "clear_room", {"roomId": room_id})
        except Exception:
            pass
        try:
            deleted = self.db.call("Room", "delete", {"id": room_id})
            if not deleted.get("deleted"):
                self.db.call("Room", "update_status", {"id": room_id, "status": "closed"})
        except Exception:
            pass

    def cleanup_user(self, player_id: int) -> None:
        """清理玩家相關資源 (離線時呼叫)"""
        # 清理玩家擁有的房間
        rooms = self.db.call("Room", "list_by_owner", {"ownerId": player_id})
        for room in rooms:
            self.cleanup_room(room["id"])
        # 從其他房間移除
        try:
            self.db.call("RoomMember", "delete_by_player", {"playerId": player_id})
        except Exception as e:
            print(f"[Lobby] cleanup_user remove membership failed: {e}")

        # Remove invites sent to or from this player
        try:
            self.db.call("Invite", "delete_by_player", {"playerId": player_id})
        except Exception as e:
            print(f"[Lobby] cleanup_user invites failed: {e}")

    def serve(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen()
            print(f"[Lobby] listening on {self.host}:{self.port}")
            while True:
                conn, addr = s.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()

    def handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        print(f"[Lobby] Client connected: {addr}")
        with self.lock:
            self.sessions[conn] = None
        try:
            while True:
                req = recv_json(conn)
                action = req.get("type")
                if action == "PING":
                    send_json(conn, {"ok": True})
                    continue
                if action == "REGISTER":
                    self.handle_register(conn, req)
                    continue
                if action == "LOGIN":
                    self.handle_login(conn, req)
                    continue
                session = self.sessions.get(conn)
                if not session:
                    send_json(conn, {"ok": False, "error": "not authenticated"})
                    continue
                if action == "LIST_GAMES":
                    games = self.db.call("Game", "list_published", {})
                    send_json(conn, {"ok": True, "games": games})
                elif action == "GET_GAME_DETAILS":
                    game_id = req["gameId"]
                    game = self.db.call("Game", "read", {"id": game_id})
                    if not game:
                        send_json(conn, {"ok": False, "error": "Game not found"})
                        continue
                    # Get developer/author info
                    developer = None
                    if game.get("owner_id"):
                        try:
                            developer = self.db.call("DeveloperAccount", "read", {"id": game["owner_id"]})
                        except Exception:
                            pass
                    versions = self.db.call("GameVersion", "list_by_game", {"gameId": game_id})
                    reviews = self.db.call("GameReview", "list_by_game", {"gameId": game_id})
                    send_json(conn, {"ok": True, "game": game, "developer": developer, "versions": versions, "reviews": reviews})
                elif action == "DOWNLOAD_GAME":
                    self.handle_download(conn, session, req)
                elif action == "LIST_ROOMS":
                    self.handle_list_rooms(conn)
                elif action == "CREATE_ROOM":
                    self.handle_create_room(conn, session, req)
                elif action == "JOIN_ROOM":
                    self.handle_join_room(conn, session, req)
                elif action == "LEAVE_ROOM":
                    self.handle_leave_room(conn, session, req)
                elif action == "GET_ROOM_DETAILS":
                    self.handle_get_room_details(conn, session, req)
                elif action == "START_GAME":
                    self.handle_start_game(conn, session, req)
                elif action == "GET_GAME":
                    self.handle_get_game(conn, session, req)
                elif action == "SUBMIT_REVIEW":
                    self.handle_submit_review(conn, session, req)
                elif action == "LIST_ACTIVE_PLAYERS":
                    self.handle_list_active_players(conn)
                elif action == "LOGOUT":
                    self.handle_logout(conn, session)
                elif action == "INVITE":
                    self.handle_invite(conn, session, req)
                elif action == "LIST_INVITES":
                    self.handle_list_invites(conn, session)
                elif action == "ACCEPT_INVITE":
                    self.handle_accept_invite(conn, session, req)
                elif action == "PLUGIN_LIST":
                    plugins = self.db.call("Plugin", "list", {})
                    installed = self.db.call(
                        "PlayerPlugin", "list_by_player", {"playerId": session.account["id"]}
                    )
                    send_json(conn, {"ok": True, "plugins": plugins, "installed": installed})
                elif action == "PLUGIN_INSTALL":
                    self.handle_plugin_install(conn, session, req)
                elif action == "PLUGIN_REMOVE":
                    self.handle_plugin_remove(conn, session, req)
                elif action == "ROOM_CHAT":
                    # Plugin 功能: 房間聊天 (Use Case PL3)
                    self.handle_room_chat(conn, session, req)
                elif action == "GET_ROOM_CHAT_HISTORY":
                    # Plugin 功能: 取得聊天記錄
                    self.handle_get_room_chat_history(conn, session, req)
                else:
                    send_json(conn, {"ok": False, "error": "unknown action"})
        except Exception as exc:
            print(f"[Lobby] Client {addr} error: {exc}")
        finally:
            with self.lock:
                session = self.sessions.pop(conn, None)
                if session:
                    print(f"[Lobby] Player '{session.account['username']}' disconnected")
                    self.cleanup_user(session.account["id"])
                else:
                    print(f"[Lobby] Client {addr} disconnected (not logged in)")
            conn.close()

    def handle_register(self, conn: socket.socket, req: Dict[str, any]) -> None:
        username = req.get("username")
        password_hash = req.get("passwordHash")
        display_name = req.get("displayName") or username
        if not username or not password_hash:
            send_json(conn, {"ok": False, "error": "missing username/password"})
            return
        exists = self.db.call("PlayerAccount", "read_by_username", {"username": username})
        if exists:
            send_json(conn, {"ok": False, "error": "username already exists"})
            return
        created = self.db.call(
            "PlayerAccount",
            "create",
            {"username": username, "passwordHash": password_hash, "displayName": display_name},
        )
        print(f"[Lobby] New player registered: '{username}' (ID: {created['id']})")
        send_json(conn, {"ok": True, "playerId": created["id"]})

    def handle_login(self, conn: socket.socket, req: Dict[str, any]) -> None:
        username = req.get("username")
        password_hash = req.get("passwordHash")
        account = self.db.call("PlayerAccount", "read_by_username", {"username": username})
        if not account or account["password_hash"] != password_hash:
            send_json(conn, {"ok": False, "error": "invalid credentials"})
            return
        
        # 檢查是否已經有其他連線登入此帳號（禁止重複登入）
        with self.lock:
            for existing_conn, existing_session in self.sessions.items():
                if existing_session and existing_session.account["id"] == account["id"]:
                    send_json(conn, {"ok": False, "error": "This account is already logged in from another session"})
                    return
        
        self.db.call("PlayerAccount", "set_last_login", {"id": account["id"]})
        session = PlayerSession(account)
        with self.lock:
            self.sessions[conn] = session
        print(f"[Lobby] Player '{username}' logged in (ID: {account['id']})")
        send_json(
            conn,
            {
                "ok": True,
                "token": session.token,
                "player": {
                    "id": account["id"],
                    "username": account["username"],
                    "displayName": account["display_name"],
                },
            },
        )

    def handle_download(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        game_id = req.get("gameId")
        version_id = req.get("versionId")
        game = self.db.call("Game", "read", {"id": game_id})
        if not game:
            send_json(conn, {"ok": False, "error": "game not found"})
            return
        
        # Check if game is available for download
        game_status = game.get("status", "").lower()
        if game_status != "published":
            if game_status == "retired":
                send_json(conn, {"ok": False, "error": f"Cannot download: '{game.get('title', 'This game')}' has been retired and is no longer available."})
            elif game_status == "draft":
                send_json(conn, {"ok": False, "error": f"Cannot download: '{game.get('title', 'This game')}' is not published yet."})
            else:
                send_json(conn, {"ok": False, "error": f"Cannot download: Game is not available (status: {game_status})."})
            return
        
        if version_id:
            version = self.db.call("GameVersion", "read", {"id": version_id})
        else:
            latest_id = game.get("latest_version_id")
            if not latest_id:
                send_json(conn, {"ok": False, "error": "no published version"})
                return
            version = self.db.call("GameVersion", "read", {"id": latest_id})
        if not version:
            send_json(conn, {"ok": False, "error": "version not found"})
            return
        path = Path(version["package_path"])
        if not path.exists():
            send_json(conn, {"ok": False, "error": "package missing"})
            return
        raw = path.read_bytes()
        
        # Compute SHA256 hash for integrity verification
        sha256_hash = hashlib.sha256(raw).hexdigest()
        
        payload = base64.b64encode(raw).decode("ascii")
        self.db.call(
            "PlayerDownload",
            "record",
            {"playerId": session.account["id"], "gameVersionId": version["id"]},
        )
        send_json(
            conn,
            {
                "ok": True,
                "game": game,
                "version": version,
                "package": payload,
                "sha256": sha256_hash,
            },
        )

    def handle_list_rooms(self, conn: socket.socket) -> None:
        rooms = self.db.call("Room", "list_open", {})
        enriched = []
        for room in rooms:
            members = self.db.call("RoomMember", "list", {"roomId": room["id"]})
            enriched.append({"room": room, "members": members})
        send_json(conn, {"ok": True, "rooms": enriched})

    def handle_create_room(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        # Check if player is already in a room
        existing_room = self.db.call("RoomMember", "find_player_room", {"playerId": session.account["id"]})
        if existing_room:
            send_json(conn, {"ok": False, "error": f"You are already in room {existing_room['code']}. Leave it first."})
            return
        
        game_id = req["gameId"]
        
        # Check if game exists and is published
        game = self.db.call("Game", "read", {"id": game_id})
        if not game:
            send_json(conn, {"ok": False, "error": "Game not found"})
            return
        
        game_status = game.get("status", "").lower()
        if game_status != "published":
            if game_status == "retired":
                send_json(conn, {"ok": False, "error": f"Cannot create room: '{game.get('title', 'This game')}' has been retired and is no longer available."})
            elif game_status == "draft":
                send_json(conn, {"ok": False, "error": f"Cannot create room: '{game.get('title', 'This game')}' is not published yet."})
            else:
                send_json(conn, {"ok": False, "error": f"Cannot create room: Game is not available (status: {game_status})."})
            return
        
        version_id = req.get("versionId")
        if not version_id:
            version_id = game.get("latest_version_id")
        version = self.db.call("GameVersion", "read", {"id": version_id})
        if not version:
            send_json(conn, {"ok": False, "error": "version not found"})
            return
        
        # Extract mode settings
        mode = req.get("mode", "timed")
        line_target = req.get("lineTarget", 20)
        time_limit = req.get("timeLimit", 180)
        visibility = req.get("visibility", "public")
        
        metadata = {
            "mode": mode,
            "lineTarget": line_target,
            "timeLimit": time_limit,
            "visibility": visibility,
        }
        
        code = self.generate_room_code()
        result = self.db.call(
            "Room",
            "create",
            {
                "code": code,
                "ownerPlayerId": session.account["id"],
                "gameId": game_id,
                "gameVersionId": version_id,
                "capacity": req.get("capacity", 4),
                "metadataJson": json.dumps(metadata),
            },
        )
        room_id = result["id"]
        self.db.call("RoomMember", "add", {"roomId": room_id, "playerId": session.account["id"]})
        print(f"[Lobby] Player '{session.account['username']}' created room '{code}' (ID: {room_id})")
        send_json(conn, {"ok": True, "roomCode": code, "roomId": room_id})

    def handle_join_room(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        # Check if player is already in a room
        existing_room = self.db.call("RoomMember", "find_player_room", {"playerId": session.account["id"]})
        if existing_room:
            # Allow if same room
            target_room_id = req.get("roomId")
            if target_room_id and existing_room["room_id"] == target_room_id:
                send_json(conn, {"ok": True, "roomId": target_room_id, "message": "Already in this room"})
                return
            send_json(conn, {"ok": False, "error": f"You are already in room {existing_room['code']}. Leave it first."})
            return
        
        room_id = req.get("roomId")
        code = req.get("roomCode")
        room: Optional[Dict[str, Any]] = None
        if room_id:
            room = self.db.call("Room", "read", {"id": room_id})
        elif code:
            room = self.db.call("Room", "read_by_code", {"code": code})
        else:
            send_json(conn, {"ok": False, "error": "missing room identifier"})
            return
        if not room:
            send_json(conn, {"ok": False, "error": "room not found"})
            return
        
        # 檢查房間狀態
        room_status = room.get("status", "waiting")
        if room_status not in ("waiting",):
            send_json(conn, {"ok": False, "error": f"cannot join room (status: {room_status})"})
            return
        
        members = self.db.call("RoomMember", "list", {"roomId": room["id"]})
        if len(members) >= room["capacity"]:
            send_json(conn, {"ok": False, "error": "room full"})
            return
        self.db.call("RoomMember", "add", {"roomId": room["id"], "playerId": session.account["id"]})
        print(f"[Lobby] Player '{session.account['username']}' joined room '{room.get('code')}' (ID: {room['id']})")
        send_json(conn, {"ok": True, "roomId": room["id"]})

    def handle_leave_room(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        room_id = req.get("roomId")
        if not room_id:
            send_json(conn, {"ok": False, "error": "missing roomId"})
            return
        try:
            room_id_int = int(room_id)
        except (TypeError, ValueError):
            send_json(conn, {"ok": False, "error": "invalid roomId"})
            return
        room = self.db.call("Room", "read", {"id": room_id_int})
        self.db.call("RoomMember", "remove", {"roomId": room_id_int, "playerId": session.account["id"]})
        room_code = room.get('code') if room else '?'
        print(f"[Lobby] Player '{session.account['username']}' left room '{room_code}' (ID: {room_id_int})")
        if room and room["owner_player_id"] == session.account["id"]:
            print(f"[Lobby] Room '{room_code}' closed (host left)")
            self.cleanup_room(room_id_int)
        send_json(conn, {"ok": True})

    def handle_get_room_details(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        room_id_val = req.get("roomId")
        try:
            room_id = int(room_id_val)
        except (TypeError, ValueError):
            send_json(conn, {"ok": False, "error": "invalid roomId"})
            return
        room = self.db.call("Room", "read", {"id": room_id})
        if not room:
            send_json(conn, {"ok": False, "error": "room not found"})
            return
        members = self.db.call("RoomMember", "list", {"roomId": room_id})
        in_room = any(member.get("player_id") == session.account["id"] for member in members)
        if not in_room:
            send_json(conn, {"ok": False, "error": "not in room"})
            return
        response: Dict[str, any] = {"ok": True, "room": room, "members": members}
        with self.lock:
            active = self.active_rooms.get(room_id)
        if active:
            launch_info = dict(active.get("info", {}))
            launch_info.setdefault("gameId", room.get("game_id"))
            response["activeLaunch"] = launch_info
            response["startedAt"] = active.get("startedAt")
        send_json(conn, response)

    def handle_start_game(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        room_id = req.get("roomId")
        room = self.db.call("Room", "read", {"id": room_id})
        if not room:
            send_json(conn, {"ok": False, "error": "room not found"})
            return
        if room["owner_player_id"] != session.account["id"]:
            send_json(conn, {"ok": False, "error": "only host can start"})
            return
        
        # 檢查房間狀態
        room_status = room.get("status", "waiting")
        if room_status != "waiting":
            send_json(conn, {"ok": False, "error": f"cannot start game (room status: {room_status})"})
            return
        
        version = self.db.call("GameVersion", "read", {"id": room["game_version_id"]})
        if not version:
            send_json(conn, {"ok": False, "error": "version missing"})
            return
        
        members = self.db.call("RoomMember", "list", {"roomId": room_id})
        
        # 檢查最少玩家數
        game = self.db.call("Game", "read", {"id": room["game_id"]})
        min_players = game.get("min_players", 1) if game else 1
        if len(members) < min_players:
            send_json(conn, {"ok": False, "error": f"need at least {min_players} player(s) to start (currently {len(members)})"})
            return
        
        try:
            process, runtime_dir, launch_info = self.launch_game_instance(room, version, members)
        except Exception as exc:
            send_json(conn, {"ok": False, "error": str(exc)})
            return
        launch_info.setdefault("gameId", room.get("game_id"))
        with self.lock:
            self.active_rooms[room_id] = {
                "process": process,
                "runtime": runtime_dir,
                "info": launch_info,
                "startedAt": time.time(),
            }
        threading.Thread(target=self.monitor_room_process, args=(room_id,), daemon=True).start()
        self.db.call("Room", "update_status", {"id": room_id, "status": "playing"})
        print(f"[Lobby] Game started in room '{room.get('code')}' (ID: {room_id}) with {len(members)} player(s)")
        send_json(conn, {"ok": True, "launch": launch_info})

    def monitor_room_process(self, room_id: int) -> None:
        with self.lock:
            entry = self.active_rooms.get(room_id)
        if not entry:
            return
        process = entry.get("process")
        runtime = entry.get("runtime")
        try:
            if process:
                process.wait()
        except Exception as exc:
            print(f"[Lobby] monitor_room_process wait failed for room {room_id}: {exc}")
        if runtime:
            try:
                shutil.rmtree(runtime, ignore_errors=True)
            except Exception:
                pass
        with self.lock:
            removed = self.active_rooms.pop(room_id, None)
        if not removed:
            return
        # Check if any room members are still online; if not, clean up the room
        try:
            room = self.db.call("Room", "read", {"id": room_id})
            if not room or room.get("status") == "closed":
                return
            members = self.db.call("RoomMember", "list", {"roomId": room_id})
            online_ids = self.get_online_player_ids()
            any_online = any(m.get("player_id") in online_ids for m in members)
            if any_online:
                self.db.call("Room", "update_status", {"id": room_id, "status": "waiting"})
                print(f"[Lobby] room {room_id} game ended, status reset to waiting")
            else:
                print(f"[Lobby] room {room_id} game ended, no online members, cleaning up")
                self.cleanup_room(room_id)
        except Exception as exc:
            print(f"[Lobby] monitor_room_process status reset failed for room {room_id}: {exc}")

    def get_online_player_ids(self) -> set:
        """Return set of player IDs currently logged in"""
        with self.lock:
            return {sess.account["id"] for sess in self.sessions.values() if sess}

    def handle_get_game(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        room_id = req.get("roomId")
        if room_id is None:
            send_json(conn, {"ok": False, "error": "missing roomId"})
            return
        with self.lock:
            entry = self.active_rooms.get(int(room_id))
        if not entry:
            send_json(conn, {"ok": False, "error": "no active game"})
            return
        room = self.db.call("Room", "read", {"id": room_id})
        if not room:
            send_json(conn, {"ok": False, "error": "room not found"})
            return
        members = self.db.call("RoomMember", "list", {"roomId": room_id})
        allowed = False
        for member in members:
            if member.get("player_id") == session.account["id"]:
                allowed = True
                break
        if not allowed:
            send_json(conn, {"ok": False, "error": "not in room"})
            return
        info = dict(entry.get("info", {}))
        info.setdefault("gameId", room.get("game_id"))
        send_json(conn, {"ok": True, "launch": info})

    def handle_submit_review(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        game_id = req.get("gameId")
        rating = req.get("rating")
        comment = req.get("comment", "")
        
        # Validate game exists
        if not game_id:
            send_json(conn, {"ok": False, "error": "請選擇要評論的遊戲"})
            return
        
        game = self.db.call("Game", "read", {"id": game_id})
        if not game:
            send_json(conn, {"ok": False, "error": "遊戲不存在"})
            return
        
        # Validate rating (支援小數，如 3.5)
        try:
            rating_float = float(rating)
        except (TypeError, ValueError):
            send_json(conn, {"ok": False, "error": "評分必須是數字"})
            return
        
        if not (1.0 <= rating_float <= 5.0):
            send_json(conn, {"ok": False, "error": "評分必須在 1.0-5.0 之間"})
            return
        
        # Check if player has played/downloaded this game
        player_id = session.account["id"]
        downloads = self.db.call("PlayerDownload", "list_versions", {"playerId": player_id})
        
        # Get all version IDs for this game
        versions = self.db.call("GameVersion", "list_by_game", {"gameId": game_id})
        game_version_ids = {v["id"] for v in versions} if versions else set()
        
        # Check if player has downloaded any version of this game
        has_played = any(d["game_version_id"] in game_version_ids for d in downloads)
        
        if not has_played:
            send_json(conn, {"ok": False, "error": "您尚未遊玩過此遊戲，無法評論"})
            return
        
        # Validate comment length
        max_comment_length = 1000
        if len(comment) > max_comment_length:
            send_json(conn, {"ok": False, "error": f"評論內容過長，請限制在 {max_comment_length} 字以內（目前 {len(comment)} 字）"})
            return
        
        # Submit review
        self.db.call(
            "GameReview",
            "upsert",
            {
                "gameId": game_id,
                "playerId": player_id,
                "rating": rating_float,
                "comment": comment,
            },
        )
        send_json(conn, {"ok": True, "message": "評論已成功送出"})

    def handle_list_active_players(self, conn: socket.socket) -> None:
        with self.lock:
            sessions = [sess for sess in self.sessions.values() if sess]
        
        # 取得每個玩家所在的房間資訊
        player_rooms: Dict[int, Optional[Dict]] = {}
        for sess in sessions:
            player_id = sess.account["id"]
            if player_id not in player_rooms:
                # 查詢玩家所在的房間
                memberships = self.db.call("RoomMember", "list_by_player", {"playerId": player_id})
                if memberships:
                    # 取得第一個房間的資訊
                    room_id = memberships[0].get("room_id")
                    room = self.db.call("Room", "read", {"id": room_id})
                    player_rooms[player_id] = room
                else:
                    player_rooms[player_id] = None
        
        players = []
        seen: set[int] = set()
        for sess in sessions:
            player_id = sess.account["id"]
            if player_id in seen:
                continue
            seen.add(player_id)
            
            # 決定玩家狀態
            room = player_rooms.get(player_id)
            if room:
                room_status = room.get("status", "waiting")
                if room_status == "playing":
                    state = "In Game"
                else:
                    state = "In Room"
            else:
                state = "Idle"
            
            players.append(
                {
                    "id": player_id,
                    "username": sess.account.get("username"),
                    "displayName": sess.account.get("display_name"),
                    "loggedInAt": sess.logged_in_at,
                    "state": state,
                }
            )
        send_json(conn, {"ok": True, "players": players})

    def handle_logout(self, conn: socket.socket, session: PlayerSession) -> None:
        """Handle user logout - cleanup their rooms and invites"""
        print(f"[Lobby] Player '{session.account['username']}' logged out")
        self.cleanup_user(session.account["id"])
        with self.lock:
            self.sessions[conn] = None
        send_json(conn, {"ok": True})

    def handle_invite(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        """Invite a player to a room - only host can invite"""
        room_id = req.get("roomId")
        to_player_id = req.get("toPlayerId")
        if not room_id or not to_player_id:
            send_json(conn, {"ok": False, "error": "missing roomId or toPlayerId"})
            return
        
        # 不能邀請自己
        if to_player_id == session.account["id"]:
            send_json(conn, {"ok": False, "error": "cannot invite yourself"})
            return
        
        # 檢查目標玩家是否存在
        target_player = self.db.call("PlayerAccount", "read", {"id": to_player_id})
        if not target_player:
            send_json(conn, {"ok": False, "error": "player not found"})
            return
        
        room = self.db.call("Room", "read", {"id": room_id})
        if not room:
            send_json(conn, {"ok": False, "error": "room not found"})
            return
        
        # 只有房主可以邀請
        if room["owner_player_id"] != session.account["id"]:
            send_json(conn, {"ok": False, "error": "only host can invite"})
            return
        
        # 檢查房間狀態
        if room.get("status") != "waiting":
            send_json(conn, {"ok": False, "error": "room is not waiting for players"})
            return
        
        # 檢查房間是否已滿
        members = self.db.call("RoomMember", "list", {"roomId": room_id})
        if len(members) >= room.get("capacity", 4):
            send_json(conn, {"ok": False, "error": "room is full"})
            return
        
        # 檢查目標玩家是否已在此房間
        for member in members:
            if member.get("player_id") == to_player_id:
                send_json(conn, {"ok": False, "error": "player is already in this room"})
                return
        
        result = self.db.call(
            "Invite",
            "create",
            {"roomId": room_id, "fromPlayerId": session.account["id"], "toPlayerId": to_player_id},
        )
        send_json(conn, {"ok": True, "inviteId": result["id"]})

    def handle_list_invites(self, conn: socket.socket, session: PlayerSession) -> None:
        """List pending invites for current player"""
        invites = self.db.call("Invite", "list_by_player", {"playerId": session.account["id"]})
        send_json(conn, {"ok": True, "invites": invites})

    def handle_accept_invite(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        """Accept an invite and join the room"""
        invite_id = req.get("inviteId")
        if not invite_id:
            send_json(conn, {"ok": False, "error": "missing inviteId"})
            return
        
        # 檢查玩家是否已在其他房間
        existing_room = self.db.call("RoomMember", "find_player_room", {"playerId": session.account["id"]})
        if existing_room:
            send_json(conn, {"ok": False, "error": f"You are already in room {existing_room['code']}. Leave it first."})
            return
        
        invite = self.db.call("Invite", "read", {"id": invite_id})
        if not invite or invite.get("to_player_id") != session.account["id"] or invite.get("status") != "pending":
            send_json(conn, {"ok": False, "error": "invalid invite"})
            return
        room_id = invite["room_id"]
        room = self.db.call("Room", "read", {"id": room_id})
        if not room:
            self.db.call("Invite", "delete_by_room", {"roomId": room_id})
            send_json(conn, {"ok": False, "error": "room not found"})
            return
        if room["status"] != "waiting":
            send_json(conn, {"ok": False, "error": "room not waiting"})
            return
        members = self.db.call("RoomMember", "list", {"roomId": room_id})
        if len(members) >= room["capacity"]:
            send_json(conn, {"ok": False, "error": "room full"})
            return
        self.db.call("RoomMember", "add", {"roomId": room_id, "playerId": session.account["id"]})
        self.db.call("Invite", "update_status", {"id": invite_id, "status": "accepted"})
        send_json(conn, {"ok": True, "roomId": room_id})

    def handle_plugin_install(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        slug = req.get("slug")
        plugin = self.db.call("Plugin", "read", {"slug": slug})
        if not plugin:
            send_json(conn, {"ok": False, "error": "plugin not found"})
            return
        path = Path(plugin["package_path"])
        if not path.exists():
            send_json(conn, {"ok": False, "error": "plugin package missing"})
            return
        raw = path.read_bytes()
        self.db.call(
            "PlayerPlugin",
            "install",
            {
                "playerId": session.account["id"],
                "pluginId": plugin["id"],
                "version": plugin["latest_version"],
            },
        )
        send_json(
            conn,
            {
                "ok": True,
                "plugin": plugin,
                "package": base64.b64encode(raw).decode("ascii"),
            },
        )

    def handle_plugin_remove(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        slug = req.get("slug")
        plugin = self.db.call("Plugin", "read", {"slug": slug})
        if not plugin:
            send_json(conn, {"ok": False, "error": "plugin not found"})
            return
        self.db.call(
            "PlayerPlugin",
            "remove",
            {"playerId": session.account["id"], "pluginId": plugin["id"]},
        )
        send_json(conn, {"ok": True})

    def handle_room_chat(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        """
        處理房間聊天訊息 (Plugin 功能)
        """
        room_id = req.get("roomId")
        message = req.get("message", "").strip()
        
        if not room_id:
            send_json(conn, {"ok": False, "error": "missing roomId"})
            return
        
        if not message:
            send_json(conn, {"ok": False, "error": "empty message"})
            return
        
        # 限制訊息長度
        if len(message) > 500:
            send_json(conn, {"ok": False, "error": "message too long (max 500 chars)"})
            return
        
        # 檢查玩家是否在此房間
        member = self.db.call("RoomMember", "find_player_room", {"playerId": session.account["id"]})
        if not member or member.get("room_id") != room_id:
            send_json(conn, {"ok": False, "error": "you are not in this room"})
            return
        
        # 儲存聊天訊息
        self.db.call("RoomChat", "create", {
            "roomId": room_id,
            "playerId": session.account["id"],
            "message": message,
        })
        
        send_json(conn, {"ok": True})

    def handle_get_room_chat_history(self, conn: socket.socket, session: PlayerSession, req: Dict[str, any]) -> None:
        """
        取得房間聊天記錄 (Plugin 功能)
        """
        room_id = req.get("roomId")
        limit = req.get("limit", 50)
        
        if not room_id:
            send_json(conn, {"ok": False, "error": "missing roomId"})
            return
        
        # 檢查玩家是否在此房間
        member = self.db.call("RoomMember", "find_player_room", {"playerId": session.account["id"]})
        if not member or member.get("room_id") != room_id:
            send_json(conn, {"ok": False, "error": "you are not in this room"})
            return
        
        # 取得聊天記錄
        messages = self.db.call("RoomChat", "list", {"roomId": room_id, "limit": limit})
        send_json(conn, {"ok": True, "messages": messages})

    def generate_room_code(self) -> str:
        while True:
            code = "".join(random.choice(CODE_ALPHABET) for _ in range(6))
            exists = self.db.call("Room", "read_by_code", {"code": code})
            if not exists:
                return code

    def prepare_runtime(self, room_id: int, version: Dict[str, Any]) -> Path:
        package_path = Path(version["package_path"])
        if not package_path.exists():
            raise FileNotFoundError(f"package missing: {package_path}")
        target = self.runtime_root / f"room_{room_id}_{version['id']}_{int(time.time())}"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(package_path, "r") as zf:
            zf.extractall(target)
        return target

    def launch_game_instance(
        self,
        room: Dict[str, any],
        version: Dict[str, any],
        members: List[Dict[str, any]],
    ) -> Tuple[subprocess.Popen, Path, Dict[str, any]]:
        runtime_dir = self.prepare_runtime(room["id"], version)
        port = find_free_port(host=self.host)
        room_token = f"rt{int(time.time())}{room['id']}"
        players = []
        for idx, member in enumerate(members, start=1):
            username = member.get("username") or f"Player{idx}"
            players.append(
                {
                    "playerId": member.get("player_id"),
                    "username": username,
                    "slot": idx,
                }
            )
        env = os.environ.copy()
        env.update(
            {
                "GAME_SERVER_HOST": self.host,
                "GAME_SERVER_PORT": str(port),
                "GAME_ROOM_ID": str(room["id"]),
                "GAME_ROOM_TOKEN": room_token,
                "LOBBY_HOST": self.host,
                "LOBBY_PORT": str(self.port),
                "GAME_VERSION_ID": str(version["id"]),
                "ROOM_PLAYERS": json.dumps(players),
                "ROOM_METADATA": room.get("metadata_json", "{}"),
            }
        )
        process = subprocess.Popen(
            version["server_entrypoint"],
            cwd=runtime_dir,
            shell=True,
            env=env,
        )
        launch_info = {
            "host": self.public_host,
            "port": port,
            "roomId": room["id"],
            "roomToken": room_token,
            "gameVersionId": version["id"],
            "clientMode": version.get("client_mode", "gui"),
            "players": players,
        }
        return process, runtime_dir, launch_info


def main() -> None:
    """
    大廳伺服器啟動入口
    支援的命令列參數:
        --host: 伺服器監聽地址 (預設 127.0.0.1)
        --port: 伺服器監聽 Port (預設 23002)
        --db-host: DB Server 地址
        --db-port: DB Server Port
        --public-host: 對外公開的 Host (用於 NAT 環境)
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=23002)
    parser.add_argument("--db-host", default="127.0.0.1")
    parser.add_argument("--db-port", type=int, default=23000)
    parser.add_argument("--public-host", default=None, help="Host/IP advertised to players")
    args = parser.parse_args()

    server = LobbyServer(args.host, args.port, args.db_host, args.db_port, public_host=args.public_host)
    server.serve()


if __name__ == "__main__":
    main()
