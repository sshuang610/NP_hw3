"""
Tetris Game Server for np_hw3 platform
Adapted from HW2 game_server - Survival mode only
"""
import argparse
import json
import os
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from lp import recv_json, send_json

BOARD_W, BOARD_H = 10, 20

# Tetromino shapes with rotation data (7-bag)
PIECES = {
    "I": [
        [(0, 1), (1, 1), (2, 1), (3, 1)],
        [(2, 0), (2, 1), (2, 2), (2, 3)],
        [(0, 2), (1, 2), (2, 2), (3, 2)],
        [(1, 0), (1, 1), (1, 2), (1, 3)],
    ],
    "O": [
        [(1, 0), (2, 0), (1, 1), (2, 1)],
        [(1, 0), (2, 0), (1, 1), (2, 1)],
        [(1, 0), (2, 0), (1, 1), (2, 1)],
        [(1, 0), (2, 0), (1, 1), (2, 1)],
    ],
    "T": [
        [(1, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (2, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (1, 2)],
        [(1, 0), (0, 1), (1, 1), (1, 2)],
    ],
    "S": [
        [(1, 0), (2, 0), (0, 1), (1, 1)],
        [(1, 0), (1, 1), (2, 1), (2, 2)],
        [(1, 1), (2, 1), (0, 2), (1, 2)],
        [(0, 0), (0, 1), (1, 1), (1, 2)],
    ],
    "Z": [
        [(0, 0), (1, 0), (1, 1), (2, 1)],
        [(2, 0), (1, 1), (2, 1), (1, 2)],
        [(0, 1), (1, 1), (1, 2), (2, 2)],
        [(1, 0), (0, 1), (1, 1), (0, 2)],
    ],
    "J": [
        [(0, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (2, 0), (1, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (2, 2)],
        [(1, 0), (1, 1), (0, 2), (1, 2)],
    ],
    "L": [
        [(2, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (1, 2), (2, 2)],
        [(0, 1), (1, 1), (2, 1), (0, 2)],
        [(0, 0), (1, 0), (1, 1), (1, 2)],
    ],
}


def rle_encode(flat: List[int]) -> str:
    """RLE encode board for efficient transmission"""
    out = []
    i = 0
    n = len(flat)
    while i < n:
        v = flat[i]
        j = i + 1
        while j < n and flat[j] == v and j - i < 255:
            j += 1
        out.append(f"{v}:{j - i}")
        i = j
    return ",".join(out)


def in_bounds(x: int, y: int) -> bool:
    return 0 <= x < BOARD_W and 0 <= y < BOARD_H


@dataclass
class Active:
    """Current falling piece"""
    shape: str
    rot: int
    x: int
    y: int


@dataclass
class Player:
    """Player state"""
    player_id: int
    username: str
    role: str
    board: List[int] = field(default_factory=lambda: [0] * (BOARD_W * BOARD_H))
    active: Optional[Active] = None
    hold: Optional[str] = None
    can_hold: bool = True
    nextq: List[str] = field(default_factory=list)
    score: int = 0
    lines: int = 0
    alive: bool = True
    drop_acc_ms: int = 0
    disconnected: bool = False


class TetrisServer:
    def __init__(self, host: str, port: int, room_id: int, room_token: str):
        self.host = host
        self.port = port
        self.room_id = room_id
        self.room_token = room_token
        
        self.clients: Dict[socket.socket, Dict] = {}
        self.players: Dict[int, Player] = {}
        self.client_by_player: Dict[int, socket.socket] = {}
        
        self.seed = random.randint(1, 2**31 - 1)
        self.random = random.Random(self.seed)
        self.bag: List[str] = []
        
        self.gravity_ms = 600
        self.start_time = 0.0
        self.tick = 0
        self.expected_players = 2
        
        self.running = True
        self.lock = threading.Lock()
        self.winner_id: Optional[int] = None
        self.end_reason: Optional[str] = None
        self.player_meta: List[Dict] = []
        self.server_socket: Optional[socket.socket] = None

    def shape_id(self, s: str) -> int:
        return list(PIECES.keys()).index(s) + 1

    def next_piece(self) -> str:
        """7-bag random generator"""
        if not self.bag:
            bag = list(PIECES.keys())
            self.random.shuffle(bag)
            self.bag.extend(bag)
        return self.bag.pop()

    def can_place(self, board: List[int], act: Active) -> bool:
        for (dx, dy) in PIECES[act.shape][act.rot]:
            x, y = act.x + dx, act.y + dy
            if not in_bounds(x, y) or board[y * BOARD_W + x] != 0:
                return False
        return True

    def spawn_piece(self, p: Player) -> bool:
        shape = p.nextq.pop(0) if p.nextq else self.next_piece()
        while len(p.nextq) < 5:
            p.nextq.append(self.next_piece())
        act = Active(shape=shape, rot=0, x=3, y=0)
        if not self.can_place(p.board, act):
            p.alive = False
            return False
        p.active = act
        p.can_hold = True
        return True

    def lock_piece(self, p: Player):
        act = p.active
        if not act:
            return
        for (dx, dy) in PIECES[act.shape][act.rot]:
            x, y = act.x + dx, act.y + dy
            if in_bounds(x, y):
                p.board[y * BOARD_W + x] = self.shape_id(act.shape)
        p.active = None
        cleared = self.clear_lines(p)
        if cleared == 1:
            p.score += 100
        elif cleared == 2:
            p.score += 300
        elif cleared == 3:
            p.score += 500
        elif cleared == 4:
            p.score += 800
        p.lines += cleared

    def clear_lines(self, p: Player) -> int:
        new_board = [0] * (BOARD_W * BOARD_H)
        ny = BOARD_H - 1
        cleared = 0
        for y in range(BOARD_H - 1, -1, -1):
            row = p.board[y * BOARD_W:(y + 1) * BOARD_W]
            if all(v != 0 for v in row):
                cleared += 1
            else:
                new_board[ny * BOARD_W:(ny + 1) * BOARD_W] = row
                ny -= 1
        p.board = new_board
        return cleared

    def try_move(self, p: Player, dx: int, dy: int, droplock=False):
        if not p.active:
            return
        act = Active(p.active.shape, p.active.rot, p.active.x + dx, p.active.y + dy)
        if self.can_place(p.board, act):
            p.active = act
            if droplock and not self.can_place(p.board, Active(act.shape, act.rot, act.x, act.y + 1)):
                self.lock_piece(p)
                self.spawn_piece(p)
        else:
            if dy > 0:
                self.lock_piece(p)
                self.spawn_piece(p)

    def rotate(self, p: Player):
        if not p.active:
            return
        new_rot = (p.active.rot + 1) % 4
        act = Active(p.active.shape, new_rot, p.active.x, p.active.y)
        for ox in [0, -1, 1, -2, 2]:
            test = Active(act.shape, act.rot, act.x + ox, act.y)
            if self.can_place(p.board, test):
                p.active = test
                return

    def hard_drop(self, p: Player):
        if not p.active:
            return
        while True:
            nxt = Active(p.active.shape, p.active.rot, p.active.x, p.active.y + 1)
            if self.can_place(p.board, nxt):
                p.active = nxt
            else:
                break
        self.lock_piece(p)
        self.spawn_piece(p)
        p.score += 2

    def hold(self, p: Player):
        if not p.active or not p.can_hold:
            return
        cur = p.active.shape
        if p.hold is None:
            p.hold = cur
            p.active = None
            self.spawn_piece(p)
        else:
            p.hold, cur = cur, p.hold
            p.active = Active(cur, 0, 3, 0)
            if not self.can_place(p.board, p.active):
                p.alive = False
        p.can_hold = False

    def apply_input(self, player_id: int, action: str):
        with self.lock:
            p = self.players.get(player_id)
            if not p or not p.alive:
                return
            if action == "LEFT":
                self.try_move(p, -1, 0)
            elif action == "RIGHT":
                self.try_move(p, 1, 0)
            elif action == "SOFT":
                self.try_move(p, 0, 1, droplock=True)
            elif action == "CW":
                self.rotate(p)
            elif action == "HARD":
                self.hard_drop(p)
            elif action == "HOLD":
                self.hold(p)

    def ready_to_tick(self) -> bool:
        with self.lock:
            return self.start_time > 0 and len(self.players) == self.expected_players

    def check_survival_victory(self) -> Optional[int]:
        with self.lock:
            alive = [p.player_id for p in self.players.values() if p.alive]
        if len(alive) >= 2:
            return None
        if len(alive) == 1:
            return alive[0]
        return -1  # All dead (draw)

    def game_loop(self):
        """Main game loop with gravity and win conditions"""
        last = time.time()
        while self.running:
            if not self.ready_to_tick():
                last = time.time()
                time.sleep(0.05)
                continue
            
            now = time.time()
            dt_ms = int((now - last) * 1000)
            last = now
            alive_players: List[int] = []
            
            with self.lock:
                self.tick += 1
                for p in list(self.players.values()):
                    if not p.alive:
                        continue
                    alive_players.append(p.player_id)
                    p.drop_acc_ms += dt_ms
                    while p.drop_acc_ms >= self.gravity_ms:
                        p.drop_acc_ms -= self.gravity_ms
                        self.try_move(p, 0, 1)
            
            alive_count = len(alive_players)
            if alive_count == 0:
                self.end_reason = "topout"
                self.winner_id = None
                self.running = False
                break
            
            if alive_count == 1 and len(self.players) >= 2 and self.end_reason is None:
                self.winner_id = alive_players[0]
                defeated = [p for p in self.players.values() if p.player_id not in alive_players]
                if any(getattr(p, "disconnected", False) for p in defeated):
                    self.end_reason = "opponent_left"
                else:
                    self.end_reason = "topout"
                self.running = False
                break
            
            # Survival mode check
            winner = self.check_survival_victory()
            if winner is not None:
                self.winner_id = None if winner == -1 else winner
                self.end_reason = "survival"
                self.running = False
                break
            
            time.sleep(0.05)
        
        self.finish_match()

    def snapshot_loop(self):
        """Broadcast snapshots periodically"""
        while self.running:
            self.broadcast_snapshot()
            time.sleep(0.2)

    def broadcast_snapshot(self):
        with self.lock:
            snaps = [self.snapshot_for(pid) for pid in self.players]
            targets = list(self.clients.keys())
        for snap in snaps:
            for conn in list(targets):
                try:
                    send_json(conn, snap)
                except Exception:
                    try:
                        targets.remove(conn)
                    except ValueError:
                        pass

    def snapshot_for(self, pid: int) -> Dict:
        p = self.players[pid]
        board = p.board[:]
        if p.active:
            for (dx, dy) in PIECES[p.active.shape][p.active.rot]:
                x, y = p.active.x + dx, p.active.y + dy
                if in_bounds(x, y):
                    board[y * BOARD_W + x] = self.shape_id(p.active.shape)
        return {
            "type": "SNAPSHOT",
            "tick": self.tick,
            "userId": p.player_id,
            "boardRLE": rle_encode(board),
            "active": {
                "shape": p.active.shape if p.active else None,
                "x": p.active.x if p.active else None,
                "y": p.active.y if p.active else None,
                "rot": p.active.rot if p.active else None,
            },
            "hold": p.hold,
            "next": p.nextq[:3],
            "score": p.score,
            "lines": p.lines,
            "alive": p.alive,
            "role": p.role,
            "mode": "survival",
            "at": int(time.time() * 1000),
        }

    def handle_client(self, conn: socket.socket):
        player_id = None
        try:
            hello = recv_json(conn)
            if hello.get("type") != "HELLO" or hello.get("roomId") != self.room_id:
                send_json(conn, {"ok": False, "error": "bad hello"})
                conn.close()
                return
            
            room_token = hello.get("roomToken") or hello.get("token")
            if room_token != self.room_token:
                send_json(conn, {"ok": False, "error": "bad token"})
                conn.close()
                return
            
            player_id = int(hello.get("playerId") or hello.get("userId"))
            username = hello.get("username", f"Player{player_id}")
            spectate = bool(hello.get("spectate"))
            
            with self.lock:
                is_player = not spectate and len(self.players) < 2
                if is_player:
                    role = "P1" if len(self.players) == 0 else "P2"
                    pl = Player(player_id=player_id, username=username, role=role)
                    while len(pl.nextq) < 5:
                        pl.nextq.append(self.next_piece())
                    self.spawn_piece(pl)
                    self.players[player_id] = pl
                    self.clients[conn] = {"playerId": player_id, "spectator": False}
                    self.client_by_player[player_id] = conn
                    self.player_meta.append({
                        "userId": player_id,
                        "playerId": player_id,
                        "username": username,
                        "role": role,
                    })
                    if self.start_time == 0 and len(self.players) == self.expected_players:
                        self.start_time = time.time()
                else:
                    role = "SPEC"
                    self.clients[conn] = {"playerId": player_id, "spectator": True}
            
            send_json(conn, {
                "type": "WELCOME",
                "role": role,
                "playerId": player_id,
                "seed": self.seed,
                "gravityPlan": {"mode": "fixed", "dropMs": self.gravity_ms},
                "mode": "survival",
                "players": self.player_meta,
            })
            
            if role == "SPEC":
                while self.running:
                    with self.lock:
                        if conn not in self.clients:
                            break
                    time.sleep(0.3)
                return
            
            while self.running:
                msg = recv_json(conn)
                if msg.get("type") == "INPUT":
                    self.apply_input(player_id, msg.get("action"))
        
        except Exception as e:
            pass
        finally:
            self.remove_connection(conn, player_id)

    def remove_connection(self, conn: socket.socket, player_id: Optional[int] = None):
        with self.lock:
            info = self.clients.pop(conn, None)
            if info and not info.get("spectator"):
                uid = info.get("playerId")
                self.client_by_player.pop(uid, None)
                player = self.players.get(uid)
                if player:
                    player.alive = False
                    player.disconnected = True
                if self.running and len(self.players) >= 2:
                    others = [p for p in self.players.values() if p.player_id != uid and p.alive]
                    if others:
                        self.winner_id = others[0].player_id
                        self.end_reason = "opponent_left"
                        self.running = False
        try:
            conn.close()
        except Exception:
            pass

    def finish_match(self):
        """Send GAME_OVER to all clients"""
        self.running = False
        if self.end_reason is None:
            self.end_reason = "finished"
        
        with self.lock:
            results = [
                {"userId": p.player_id, "score": p.score, "lines": p.lines, "alive": p.alive}
                for p in self.players.values()
            ]
        
        pkt = {
            "type": "GAME_OVER",
            "summary": results,
            "winnerId": self.winner_id,
            "mode": "survival",
            "reason": self.end_reason,
            "players": self.player_meta,
        }
        
        for conn in list(self.clients.keys()):
            try:
                send_json(conn, pkt)
            except Exception:
                pass

    def serve(self):
        print(f"[Tetris] Starting server on {self.host}:{self.port}")
        print(f"[Tetris] Room {self.room_id}, Mode: survival")
        
        threading.Thread(target=self.game_loop, daemon=True).start()
        threading.Thread(target=self.snapshot_loop, daemon=True).start()
        
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.settimeout(1.0)  # Allow periodic check of running flag
        server.bind((self.host, self.port))
        server.listen()
        self.server_socket = server
        print(f"[Tetris] Listening on {self.host}:{self.port}")
        
        try:
            while self.running:
                try:
                    conn, addr = server.accept()
                    threading.Thread(target=self.handle_client, args=(conn,), daemon=True).start()
                except socket.timeout:
                    continue
                except OSError:
                    break
        finally:
            server.close()
        
        print(f"[Tetris] Room {self.room_id} server shutting down")
        time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("GAME_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("GAME_SERVER_PORT", "0")))
    args = parser.parse_args()
    
    room_id = int(os.getenv("GAME_ROOM_ID", "0"))
    room_token = os.getenv("GAME_ROOM_TOKEN", "")
    
    if not room_id or not room_token:
        print("[Tetris] Error: Missing GAME_ROOM_ID or GAME_ROOM_TOKEN environment variables")
        return
    
    server = TetrisServer(args.host, args.port, room_id, room_token)
    server.serve()


if __name__ == "__main__":
    main()
