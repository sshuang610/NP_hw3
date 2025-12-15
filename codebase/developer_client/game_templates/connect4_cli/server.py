"""
Power Connect Four Server for np_hw3 platform
Adapted from HW1 - includes lift skill mechanic
"""
import json
import os
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from lp import recv_json, send_json

BOARD_ROWS = 6
BOARD_COLS = 7
PLAYER_SYMBOLS = {0: "X", 1: "O", 2: "A", 3: "B"}


@dataclass
class PlayerConn:
    slot: int
    player_id: int
    display_name: str
    username: str
    sock: socket.socket
    skill_used: bool = False  # Lift skill


class Connect4Server:
    def __init__(self) -> None:
        self.host = os.getenv("GAME_SERVER_HOST", "127.0.0.1")
        self.port = int(os.getenv("GAME_SERVER_PORT", "31000"))
        self.room_id = int(os.getenv("GAME_ROOM_ID", "0"))
        self.room_token = os.getenv("GAME_ROOM_TOKEN", "")
        players_raw = os.getenv("ROOM_PLAYERS", "[]")
        try:
            self.player_meta = json.loads(players_raw)
        except json.JSONDecodeError:
            self.player_meta = []
        if len(self.player_meta) < 2:
            raise RuntimeError("connect4 requires at least 2 players in ROOM_PLAYERS metadata")
        self.player_meta = self.player_meta[:2]
        self.expected_players = len(self.player_meta)
        self.lock = threading.Lock()
        self.slots: Dict[int, PlayerConn] = {}
        self.moves: "queue.Queue[Tuple[int, Dict]]" = queue.Queue()
        self.board: List[List[int]] = [[0 for _ in range(BOARD_COLS)] for _ in range(BOARD_ROWS)]
        self.current_slot = 0
        self.started = threading.Event()
        self.finished = threading.Event()
        self.winner_slot: Optional[int] = None
        self.finish_reason: str = ""
        self.server_socket: Optional[socket.socket] = None

    # --------------------------- networking ---------------------------
    def serve(self) -> None:
        threading.Thread(target=self.game_loop, daemon=True).start()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.settimeout(1.0)  # Allow periodic check of finished flag
        server.bind((self.host, self.port))
        server.listen()
        self.server_socket = server
        print(f"[Connect4] room {self.room_id} listening on {self.host}:{self.port}")
        try:
            while not self.finished.is_set():
                try:
                    conn, addr = server.accept()
                    threading.Thread(target=self.handle_connection, args=(conn, addr), daemon=True).start()
                except socket.timeout:
                    continue
                except OSError:
                    break
        finally:
            server.close()
        self.finished.wait(timeout=1.0)
        print(f"[Connect4] room {self.room_id} server shutting down")
        time.sleep(0.2)

    def handle_connection(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        try:
            hello = recv_json(conn)
        except Exception:
            conn.close()
            return
        if hello.get("type") != "HELLO" or hello.get("roomToken") != self.room_token:
            try:
                send_json(conn, {"ok": False, "error": "invalid handshake"})
            except Exception:
                pass
            conn.close()
            return
        player_id = int(hello.get("playerId", 0))
        slot = self.resolve_slot(player_id)
        if slot is None:
            try:
                send_json(conn, {"ok": False, "error": "player not allowed"})
            except Exception:
                pass
            conn.close()
            return
        with self.lock:
            if slot in self.slots:
                try:
                    send_json(conn, {"ok": False, "error": "duplicate login"})
                except Exception:
                    pass
                conn.close()
                return
            meta = self.player_meta[slot]
            player = PlayerConn(
                slot=slot,
                player_id=player_id,
                display_name=meta.get("username") or f"Player{slot + 1}",
                username=meta.get("username") or f"player{slot + 1}",
                sock=conn,
            )
            self.slots[slot] = player
        print(f"[Connect4] player {player.display_name} connected from {addr}")
        try:
            send_json(
                conn,
                {
                    "type": "WELCOME",
                    "slot": slot,
                    "symbol": PLAYER_SYMBOLS.get(slot, str(slot + 1)),
                    "board": self.board_snapshot(),
                    "players": self.players_payload(),
                    "skillAvailable": True,  # Lift skill
                },
            )
        except Exception:
            self.drop_player(slot, "handshake failed")
            return
        self.maybe_start()
        try:
            while not self.finished.is_set():
                msg = recv_json(conn)
                if msg.get("type") == "MOVE":
                    # Support both simple drop and lift+drop
                    self.moves.put((slot, msg))
        except Exception:
            self.drop_player(slot, "disconnected")
        finally:
            conn.close()

    def drop_player(self, slot: int, reason: str) -> None:
        with self.lock:
            player = self.slots.pop(slot, None)
            if player is None:
                return
        print(f"[Connect4] player slot {slot} dropped: {reason}")
        if not self.finished.is_set():
            self.winner_slot = next((s for s in range(self.expected_players) if s != slot and s in self.slots), None)
            self.finish_reason = "disconnect"
            self.finished.set()
            self.broadcast(
                {
                    "type": "GAME_OVER",
                    "winnerSlot": self.winner_slot,
                    "reason": self.finish_reason,
                    "board": self.board_snapshot(),
                }
            )

    # --------------------------- gameplay ---------------------------
    def maybe_start(self) -> None:
        if self.started.is_set():
            return
        with self.lock:
            if len(self.slots) == self.expected_players:
                self.started.set()
                self.broadcast(
                    {
                        "type": "READY",
                        "players": self.players_payload(),
                        "board": self.board_snapshot(),
                    }
                )

    def game_loop(self) -> None:
        self.started.wait()
        if self.finished.is_set():
            return
        print("[Connect4] match starting")
        while not self.finished.is_set():
            current = self.current_slot
            player = self.slots.get(current)
            if not player:
                break
            # Get liftable columns for this player
            liftable = self.get_liftable_columns(current)
            try:
                send_json(
                    player.sock,
                    {
                        "type": "YOUR_TURN",
                        "board": self.board_snapshot(),
                        "columnCount": BOARD_COLS,
                        "skillAvailable": not player.skill_used,
                        "liftableColumns": liftable,
                    },
                )
            except Exception:
                self.drop_player(current, "send failed")
                break
            move_slot, move_data = self.wait_for_move(current)
            if move_slot is None:
                continue
            
            # Handle move type
            move_kind = move_data.get("kind", "drop")
            if move_kind == "lift":
                # Lift skill: remove bottom piece and redrop
                source = move_data.get("source", -1)
                target = move_data.get("target", -1)
                applied = self.apply_lift_move(move_slot, source, target)
            else:
                # Normal drop
                column = int(move_data.get("column", -1))
                applied = self.apply_move(move_slot, column)
            
            if not applied:
                self.notify_invalid(move_slot, move_data, "invalid move")
                continue
            
            winner = self.check_winner(move_slot)
            if winner is not None:
                self.finish_reason = "win" if winner >= 0 else "draw"
                self.winner_slot = winner if winner >= 0 else None
                self.finished.set()
            else:
                self.current_slot = (self.current_slot + 1) % self.expected_players
            
            self.broadcast(
                {
                    "type": "BOARD_STATE",
                    "board": self.board_snapshot(),
                    "lastMove": {"slot": move_slot, "kind": move_kind, "data": move_data},
                    "nextSlot": None if self.finished.is_set() else self.current_slot,
                }
            )
        if not self.finish_reason:
            self.finish_reason = "aborted"
        self.broadcast(
            {
                "type": "GAME_OVER",
                "winnerSlot": self.winner_slot,
                "reason": self.finish_reason,
                "board": self.board_snapshot(),
            }
        )
        self.finished.set()

    def wait_for_move(self, expected_slot: int) -> Tuple[Optional[int], Dict]:
        while not self.finished.is_set():
            try:
                slot, move_data = self.moves.get(timeout=0.5)
            except queue.Empty:
                continue
            if slot != expected_slot:
                self.notify_invalid(slot, move_data, "not your turn")
                continue
            return slot, move_data
        return None, {}

    def apply_move(self, slot: int, column: int) -> bool:
        if column < 0 or column >= BOARD_COLS:
            return False
        # drop from bottom up
        for row in range(BOARD_ROWS - 1, -1, -1):
            if self.board[row][column] == 0:
                self.board[row][column] = slot + 1
                return True
        return False

    def apply_lift_move(self, slot: int, source: int, target: int) -> bool:
        """Lift skill: remove bottom piece from source column and drop in target"""
        player = self.slots.get(slot)
        if not player:
            return False
        if player.skill_used:
            return False
        if source < 0 or source >= BOARD_COLS or target < 0 or target >= BOARD_COLS:
            return False
        
        # Check if bottom of source column belongs to this player
        token = slot + 1
        bottom_row = BOARD_ROWS - 1
        if self.board[bottom_row][source] != token:
            return False
        
        # Remove the bottom piece and shift column down
        for row in range(BOARD_ROWS - 1, 0, -1):
            self.board[row][source] = self.board[row - 1][source]
        self.board[0][source] = 0
        
        # Mark skill as used
        player.skill_used = True
        
        # Now drop the piece in target column
        return self.apply_move(slot, target)

    def get_liftable_columns(self, slot: int) -> List[int]:
        """Get columns where the bottom piece belongs to this player"""
        token = slot + 1
        liftable = []
        for col in range(BOARD_COLS):
            if self.board[BOARD_ROWS - 1][col] == token:
                liftable.append(col)
        return liftable

    def notify_invalid(self, slot: int, move_data: Dict, extra: str = "invalid move") -> None:
        player = self.slots.get(slot)
        if not player:
            return
        try:
            send_json(player.sock, {"type": "INVALID_MOVE", "move": move_data, "message": extra})
        except Exception:
            self.drop_player(slot, "invalid move send failed")

    def check_winner(self, slot: int) -> Optional[int]:
        token = slot + 1
        # horizontal, vertical, diagonal checks
        directions = [(1, 0), (0, 1), (1, 1), (1, -1)]
        for row in range(BOARD_ROWS):
            for col in range(BOARD_COLS):
                if self.board[row][col] != token:
                    continue
                for dx, dy in directions:
                    if self.count_line(row, col, dx, dy, token) >= 4:
                        return slot
        if all(self.board[0][c] != 0 for c in range(BOARD_COLS)):
            return -1
        return None

    def count_line(self, row: int, col: int, dx: int, dy: int, token: int) -> int:
        count = 0
        r, c = row, col
        while 0 <= r < BOARD_ROWS and 0 <= c < BOARD_COLS and self.board[r][c] == token:
            count += 1
            r += dy
            c += dx
        return count

    # --------------------------- helpers ---------------------------
    def resolve_slot(self, player_id: int) -> Optional[int]:
        for idx, meta in enumerate(self.player_meta):
            if int(meta.get("playerId", meta.get("player_id", -1))) == player_id:
                return idx
            if int(meta.get("id", -1)) == player_id:
                return idx
        # fallback to order if metadata missing ids
        for idx, meta in enumerate(self.player_meta):
            if meta.get("slot") == player_id:
                return idx
        return None

    def players_payload(self) -> List[Dict]:
        items: List[Dict] = []
        for idx, meta in enumerate(self.player_meta):
            player = self.slots.get(idx)
            items.append(
                {
                    "slot": idx,
                    "displayName": meta.get("username") or f"Player{idx + 1}",
                    "symbol": PLAYER_SYMBOLS.get(idx, str(idx + 1)),
                    "skillUsed": player.skill_used if player else False,
                }
            )
        return items

    def board_snapshot(self) -> List[List[int]]:
        return [row[:] for row in self.board]

    def broadcast(self, payload: Dict) -> None:
        dead_slots = []
        for slot, player in list(self.slots.items()):
            try:
                send_json(player.sock, payload)
            except Exception:
                dead_slots.append(slot)
        for slot in dead_slots:
            self.drop_player(slot, "broadcast failed")


def main() -> None:
    server = Connect4Server()
    server.serve()


if __name__ == "__main__":
    main()
