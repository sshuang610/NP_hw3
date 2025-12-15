"""
Tetris GUI Client for np_hw3 platform
Adapted from HW2 game_client - Survival mode
"""
import argparse
import os
import socket
import sys
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk, messagebox

from lp import recv_json, send_json


COLOR = {
    0: "#111",
    1: "#6cf",
    2: "#fd6",
    3: "#c6f",
    4: "#6f6",
    5: "#f66",
    6: "#69f",
    7: "#fa6",
}

PREVIEW_SHAPES = {
    "I": [(0, 1), (1, 1), (2, 1), (3, 1)],
    "O": [(1, 0), (2, 0), (1, 1), (2, 1)],
    "T": [(1, 0), (0, 1), (1, 1), (2, 1)],
    "S": [(1, 0), (2, 0), (0, 1), (1, 1)],
    "Z": [(0, 0), (1, 0), (1, 1), (2, 1)],
    "J": [(0, 0), (0, 1), (1, 1), (2, 1)],
    "L": [(2, 0), (0, 1), (1, 1), (2, 1)],
}
PIECE_COLORS = {
    "I": COLOR[1],
    "O": COLOR[2],
    "T": COLOR[3],
    "S": COLOR[4],
    "Z": COLOR[5],
    "J": COLOR[6],
    "L": COLOR[7],
}


class GameClient:
    def __init__(
        self,
        host: str,
        port: int,
        room_id: int,
        user_id: int,
        token: str,
        username: str = "",
        root: Optional[tk.Misc] = None,
        on_back_to_lobby: Optional[Callable[[], None]] = None,
        spectate: bool = False,
    ):
        self.host = host
        self.port = port
        self.room_id = room_id
        self.user_id = user_id
        self.username = username or f"Player{user_id}"
        self.token = token
        self.seq = 1
        self.primary_user_id = user_id
        self.secondary_user_id: Optional[int] = None
        self.role = "?"
        self.current_mode: Optional[str] = "survival"
        self.game_over = False
        self.on_back_to_lobby = on_back_to_lobby
        self.last_lines: Dict[int, int] = {}
        self.sock: Optional[socket.socket] = None
        self.snapshots: Dict[int, Dict] = {}
        self.player_meta: List[Dict] = []
        self.player_order: List[int] = []
        self.is_spectator = spectate
        self.flash_until: Dict[int, float] = {}
        self.render_delay = 0.15
        self.snapshot_history: Dict[int, Deque[Tuple[float, Dict]]] = {}

        self.own_root = False
        if root is None:
            self.root = tk.Tk()
            self.root.title("Tetris Duel - Survival")
            self.own_root = True
            container = self.root
        else:
            container = root
            self.root = root.winfo_toplevel()

        self.primary_cell = 22
        self.opp_cell = 12

        self.frame = tk.Frame(container, bg="#05060a")
        self.frame.grid(row=0, column=0, sticky="nsew")
        self.content = tk.Frame(self.frame, bg="", padx=10, pady=10)
        self.content.pack(fill=tk.BOTH, expand=True)

        top_info = tk.Frame(self.content, bg="")
        top_info.pack(fill=tk.X, pady=(0, 2))
        names = tk.Frame(top_info, bg="")
        names.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.primary_name_var = tk.StringVar(value="You")
        self.secondary_name_var = tk.StringVar(value="Opponent")
        tk.Label(names, textvariable=self.primary_name_var, font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT, padx=8)
        tk.Label(names, textvariable=self.secondary_name_var, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=8)

        stats = tk.Frame(self.content, bg="")
        stats.pack(fill=tk.X, pady=(2, 2))
        self.lines_self_var = tk.StringVar(value="Lines: 0 | Score: 0")
        self.lines_opp_var = tk.StringVar(value="Lines: 0 | Score: 0")
        tk.Label(stats, textvariable=self.lines_self_var).pack(side=tk.LEFT, padx=6)
        tk.Label(stats, textvariable=self.lines_opp_var).pack(side=tk.LEFT, padx=6)

        self.mode_var = tk.StringVar(value="Mode: Survival")
        self.timer_var = tk.StringVar(value="Last player alive wins")
        tk.Label(self.content, textvariable=self.mode_var, font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(4, 0))
        tk.Label(self.content, textvariable=self.timer_var).pack(anchor="w")

        self.info = tk.StringVar(value="Connecting...")
        tk.Label(self.content, textvariable=self.info, fg="#dddddd").pack(anchor="w", pady=(4, 0))

        boards = tk.Frame(self.content, bg="")
        boards.pack()
        boards.columnconfigure(0, weight=0)
        boards.columnconfigure(1, weight=0)
        self.canvas = tk.Canvas(
            boards,
            width=10 * self.primary_cell,
            height=20 * self.primary_cell,
            bg="#050b16",
            highlightthickness=0,
        )
        self.canvas.grid(row=0, column=0, sticky="n", padx=(0, 10), pady=4)

        opp_block = tk.Frame(boards, bg="")
        opp_block.grid(row=0, column=1, sticky="s", padx=4, pady=(10, 4))
        preview_panel = tk.Frame(opp_block, bg="")
        preview_panel.pack(anchor="center", pady=(0, 6))
        ttk.Label(preview_panel, text="Next", font=("Segoe UI", 10, "bold")).pack(anchor="center")
        self.preview_canvas = tk.Canvas(preview_panel, width=90, height=60, bg="#0b121f", highlightthickness=0)
        self.preview_canvas.pack(pady=(4, 0))
        ttk.Label(opp_block, text="Opponent", font=("Segoe UI", 10, "bold")).pack(anchor="center")
        self.canvas_opp = tk.Canvas(
            opp_block,
            width=10 * self.opp_cell,
            height=20 * self.opp_cell,
            bg="#0a0f1c",
            highlightthickness=0,
        )
        self.canvas_opp.pack(padx=2, pady=(4, 0))

        controls = tk.Frame(self.content, bg="")
        controls.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(controls, text="Leave Match", command=self.leave_match).pack(side=tk.LEFT)
        ttk.Button(controls, text="Back to Lobby", command=self.back_to_lobby).pack(side=tk.RIGHT)

        bind_root = self.root if self.own_root else container.winfo_toplevel()
        bind_root.bind("<Left>", lambda e: self.send_input("LEFT"))
        bind_root.bind("<Right>", lambda e: self.send_input("RIGHT"))
        bind_root.bind("<Up>", lambda e: self.send_input("CW"))
        bind_root.bind("<Down>", lambda e: self.send_input("SOFT"))
        bind_root.bind("<space>", lambda e: self.send_input("HARD"))
        bind_root.bind("<Shift_L>", lambda e: self.send_input("HOLD"))

    def connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        for i in range(10):
            try:
                s.connect((self.host, self.port))
                break
            except ConnectionRefusedError:
                print(f"Connection failed, retrying ({i+1}/10)...")
                time.sleep(0.5)
        else:
            self.info.set("Failed to connect to server")
            return

        send_json(
            s,
            {
                "type": "HELLO",
                "version": 1,
                "roomId": self.room_id,
                "playerId": self.user_id,
                "userId": self.user_id,
                "username": self.username,
                "roomToken": self.token,
                "spectate": self.is_spectator,
            },
        )
        resp = recv_json(s)
        if resp.get("type") != "WELCOME":
            raise RuntimeError("failed to join game")
        self.role = resp.get("role", "?")
        self.is_spectator = self.role == "SPEC"
        self.player_meta = resp.get("players") or []
        self.player_order = [
            item.get("userId") or item.get("playerId")
            for item in self.player_meta
            if item.get("userId") is not None or item.get("playerId") is not None
        ]
        self.assign_view_targets()
        self.current_mode = resp.get("mode", "survival")
        self.info.set("Spectating match" if self.is_spectator else f"Joined as {self.role}")
        self.mode_var.set("Mode: Survival")
        self.timer_var.set("Last player alive wins")
        self.sock = s
        threading.Thread(target=self.recv_loop, daemon=True).start()
        self.root.after(100, self.render)

    def send_input(self, action: str):
        if not self.sock or self.is_spectator or self.game_over:
            return
        pkt = {"type": "INPUT", "userId": self.user_id, "seq": self.seq, "ts": int(time.time() * 1000), "action": action}
        self.seq += 1
        try:
            send_json(self.sock, pkt)
        except Exception:
            pass

    def recv_loop(self):
        try:
            while True:
                msg = recv_json(self.sock)
                if msg.get("type") == "SNAPSHOT":
                    self.handle_snapshot(msg)
                elif msg.get("type") == "GAME_OVER":
                    self.root.after(0, self.handle_game_over, msg)
                    break
        except Exception:
            if not self.game_over:
                self.root.after(0, lambda: self.info.set("Disconnected"))

    def handle_snapshot(self, snap: Dict) -> None:
        # Handle per-player snapshot format (from HW2)
        uid = snap.get("userId")
        if uid is not None:
            player_snap = {
                "userId": uid,
                "boardRLE": snap.get("boardRLE", ""),
                "score": snap.get("score", 0),
                "lines": snap.get("lines", 0),
                "next": snap.get("next", []),
                "hold": snap.get("hold"),
                "alive": snap.get("alive", True),
                "role": snap.get("role"),
            }
            self.snapshots[uid] = player_snap
            self.record_snapshot(uid, player_snap)
            self.update_stats_for(uid, player_snap)
            if uid == self.primary_user_id:
                self.update_preview(player_snap.get("next"))
            prev = self.last_lines.get(uid, 0)
            cur = player_snap.get("lines", 0)
            if cur > prev:
                self.flash_until[uid] = time.time() + 0.25
            self.last_lines[uid] = cur

    def record_snapshot(self, uid: int, snap: Dict) -> None:
        history = self.snapshot_history.setdefault(uid, deque())
        now_ts = time.time()
        history.append((now_ts, snap))
        cutoff = now_ts - max(self.render_delay * 4, 1.0)
        while history and history[0][0] < cutoff:
            history.popleft()

    def get_buffered_snapshot(self, user_id: Optional[int]) -> Optional[Dict]:
        if user_id is None:
            return None
        history = self.snapshot_history.get(user_id)
        if not history:
            return self.snapshots.get(user_id)
        target = time.time() - self.render_delay
        candidate: Optional[Dict] = None
        for ts, snap in history:
            if ts <= target:
                candidate = snap
            else:
                break
        if candidate is not None:
            return candidate
        return history[0][1]

    def render_board(self, canvas: tk.Canvas, rle: str, cell: int, highlight: bool = False):
        canvas.delete("all")
        flat = []
        if rle:
            for tok in rle.split(","):
                parts = tok.split(":")
                if len(parts) == 2:
                    v, c = parts
                    flat.extend([int(v)] * int(c))
        for y in range(20):
            for x in range(10):
                v = flat[y * 10 + x] if y * 10 + x < len(flat) else 0
                color = COLOR.get(v, "#333")
                canvas.create_rectangle(x * cell, y * cell, (x + 1) * cell, (y + 1) * cell, fill=color, outline="#111")
        if highlight:
            canvas.create_rectangle(0, 0, 10 * cell, 20 * cell, fill="#ffffff", outline="", stipple="gray25")

    def render(self):
        if self.game_over:
            return
        snap_primary = self.get_buffered_snapshot(self.primary_user_id)
        if snap_primary:
            self.render_board(
                self.canvas,
                snap_primary.get("boardRLE", ""),
                self.primary_cell,
                self.flash_active(self.primary_user_id),
            )
        else:
            self.canvas.delete("all")
        if self.secondary_user_id:
            snap_secondary = self.get_buffered_snapshot(self.secondary_user_id)
            if snap_secondary:
                self.render_board(
                    self.canvas_opp,
                    snap_secondary.get("boardRLE", ""),
                    self.opp_cell,
                    self.flash_active(self.secondary_user_id),
                )
            else:
                self.canvas_opp.delete("all")
        self.root.after(100, self.render)

    def flash_active(self, user_id: Optional[int]) -> bool:
        if user_id is None:
            return False
        return time.time() < self.flash_until.get(user_id, 0)

    def run(self):
        self.connect()
        if self.own_root:
            self.root.mainloop()

    def update_stats_for(self, user_id: int, snap: Dict) -> None:
        text = f"{self.player_name(user_id)}: {snap.get('lines', 0)} lines | {snap.get('score', 0)} pts"
        if user_id == self.primary_user_id:
            self.lines_self_var.set(text)
        elif self.secondary_user_id and user_id == self.secondary_user_id:
            self.lines_opp_var.set(text)

    def player_name(self, user_id: Optional[int]) -> str:
        if user_id is None:
            return "Unknown"
        for meta in self.player_meta:
            meta_id = meta.get("userId") or meta.get("playerId")
            if meta_id == user_id:
                username = meta.get("username")
                if username:
                    return f"@{username}"
                return f"Player {user_id}"
        return f"Player {user_id}"

    def assign_view_targets(self) -> None:
        if self.is_spectator and self.player_order:
            self.primary_user_id = self.player_order[0]
            self.secondary_user_id = self.player_order[1] if len(self.player_order) > 1 else None
        else:
            self.primary_user_id = self.user_id
            others = [uid for uid in self.player_order if uid != self.user_id]
            self.secondary_user_id = others[0] if others else None
        self.update_name_labels()

    def update_name_labels(self) -> None:
        self.primary_name_var.set(self.player_name(self.primary_user_id))
        if self.secondary_user_id:
            self.secondary_name_var.set(self.player_name(self.secondary_user_id))
        else:
            self.secondary_name_var.set("Waiting for opponent")

    def update_preview(self, next_list: Optional[List[str]]) -> None:
        """Display only the first next piece (like HW2)"""
        if not hasattr(self, "preview_canvas"):
            return
        self.preview_canvas.delete("all")
        width = int(self.preview_canvas["width"])
        height = int(self.preview_canvas["height"])
        if not next_list:
            self.preview_canvas.create_text(width / 2, height / 2, text="--", fill="#888")
            return
        # Only show the first piece
        shape = next_list[0]
        coords = PREVIEW_SHAPES.get(shape)
        if not coords:
            self.preview_canvas.create_text(width / 2, height / 2, text=shape or "?", fill="#888")
            return
        cell = 18
        min_x = min(x for x, _ in coords)
        max_x = max(x for x, _ in coords)
        min_y = min(y for _, y in coords)
        max_y = max(y for _, y in coords)
        shape_w = (max_x - min_x + 1) * cell
        shape_h = (max_y - min_y + 1) * cell
        offset_x = (width - shape_w) / 2
        offset_y = (height - shape_h) / 2
        color = PIECE_COLORS.get(shape, "#fefefe")
        for (x, y) in coords:
            px = offset_x + (x - min_x) * cell
            py = offset_y + (y - min_y) * cell
            self.preview_canvas.create_rectangle(px, py, px + cell - 2, py + cell - 2, fill=color, outline="#0f172a", width=2)

    def cleanup_socket(self) -> None:
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def leave_match(self) -> None:
        if messagebox.askyesno("Leave Match", "Leave the current match and return to the lobby?"):
            self.back_to_lobby()

    def back_to_lobby(self) -> None:
        self.game_over = True
        self.cleanup_socket()
        if self.on_back_to_lobby:
            try:
                self.on_back_to_lobby()
            except Exception:
                pass
        if self.own_root:
            try:
                self.root.destroy()
            except Exception:
                pass
        else:
            try:
                self.frame.destroy()
            except Exception:
                pass

    def handle_game_over(self, msg: Dict) -> None:
        if self.game_over:
            return
        self.game_over = True
        summary = msg.get("summary", []) or []
        lines = []
        for entry in summary:
            name = self.player_name(entry.get("userId"))
            lines.append(f"{name}: {entry.get('lines', 0)} lines | {entry.get('score', 0)} pts")
        winner_id = msg.get("winnerId")
        if winner_id is None:
            outcome = "Draw."
        elif winner_id == self.user_id and not self.is_spectator:
            outcome = "You win!"
        elif not self.is_spectator and winner_id != self.user_id:
            outcome = "You lose."
        else:
            outcome = f"{self.player_name(winner_id)} wins!"
        reason = msg.get("reason")
        if reason:
            reason_map = {
                "topout": "top out",
                "survival": "last player alive",
                "opponent_left": "opponent left",
            }
            label = reason_map.get(reason, reason)
            outcome = f"{outcome} ({label})"
        text = outcome + "\n" + "\n".join(lines)
        self.info.set(text)
        summary_win = tk.Toplevel(self.root)
        summary_win.title("Game Over")
        tk.Label(summary_win, text=text, justify=tk.LEFT).pack(padx=12, pady=12)
        ttk.Button(summary_win, text="Back to Lobby", command=lambda: self._close_summary(summary_win)).pack(pady=(0, 12))
        summary_win.transient(self.root)
        summary_win.grab_set()
        self.cleanup_socket()

    def _close_summary(self, summary_win: tk.Toplevel) -> None:
        if summary_win.winfo_exists():
            summary_win.destroy()
        self.back_to_lobby()


def main() -> None:
    parser = argparse.ArgumentParser(description="Tetris GUI client")
    parser.add_argument("--host", default=os.getenv("GAME_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("GAME_PORT", "31000")))
    parser.add_argument("--room-id", type=int, default=int(os.getenv("GAME_ROOM_ID", "0")))
    parser.add_argument("--user-id", type=int, default=int(os.getenv("PLAYER_ID", "0")))
    parser.add_argument("--username", default=os.getenv("PLAYER_USERNAME", ""))
    parser.add_argument("--token", default=os.getenv("GAME_ROOM_TOKEN", ""))
    parser.add_argument("--spectate", action="store_true", help="Join as spectator")
    args = parser.parse_args()

    host = os.getenv("GAME_HOST", args.host)
    port = int(os.getenv("GAME_PORT", args.port))
    room_id = int(os.getenv("GAME_ROOM_ID", args.room_id))
    user_id = int(os.getenv("PLAYER_ID", args.user_id))
    username = os.getenv("PLAYER_USERNAME", args.username) or f"Player{user_id}"
    token = os.getenv("GAME_ROOM_TOKEN", args.token)
    if not port or not token or not room_id:
        raise SystemExit("Missing GAME_PORT/GAME_ROOM_TOKEN/GAME_ROOM_ID environment variables")

    client = GameClient(host, port, room_id, user_id, token, username=username, spectate=args.spectate)
    client.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        try:
            messagebox.showerror("Game Error", f"Critical error: {e}")
        except:
            pass
        input("Press Enter to exit...")
        sys.exit(1)
