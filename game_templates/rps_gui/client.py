"""
Rock-Paper-Scissors Multiplayer GUI Client
Supports 3-8 players with Tkinter interface
"""
import argparse
import os
import socket
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

import tkinter as tk
from tkinter import ttk, messagebox

from lp import recv_json, send_json

# Colors for players
PLAYER_COLORS = [
    "#FF6B6B",  # Red
    "#4ECDC4",  # Teal
    "#45B7D1",  # Blue
    "#96CEB4",  # Green
    "#FFEAA7",  # Yellow
    "#DDA0DD",  # Plum
    "#98D8C8",  # Mint
    "#F7DC6F",  # Gold
]

# Choice emojis/symbols - 使用手勢圖案
CHOICE_DISPLAY = {
    "rock": "✊ Rock",
    "paper": "🖐️ Paper",
    "scissors": "✌️ Scissors",
    None: "❓ Waiting...",
}

CHOICE_ICONS = {
    "rock": "✊",
    "paper": "🖐️",
    "scissors": "✌️",
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
        self.role = "?"
        self.game_over = False
        self.on_back_to_lobby = on_back_to_lobby
        self.sock: Optional[socket.socket] = None
        self.is_spectator = spectate
        self.is_ready = False
        self.my_choice: Optional[str] = None
        
        # Game state
        self.game_phase = "waiting"
        self.current_round = 0
        self.total_rounds = 5
        self.time_remaining = 0
        self.players_data: List[Dict] = []
        self.player_meta: List[Dict] = []

        self.own_root = False
        if root is None:
            self.root = tk.Tk()
            self.root.title("Rock Paper Scissors - Multiplayer")
            self.own_root = True
            container = self.root
        else:
            container = root
            self.root = root.winfo_toplevel()

        # Main frame
        self.frame = tk.Frame(container, bg="#1a1a2e")
        self.frame.grid(row=0, column=0, sticky="nsew")
        
        # Configure grid
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)
        
        # Header
        header = tk.Frame(self.frame, bg="#16213e", padx=10, pady=10)
        header.grid(row=0, column=0, sticky="ew")
        
        self.title_var = tk.StringVar(value="Rock Paper Scissors - Multiplayer")
        tk.Label(header, textvariable=self.title_var, font=("Segoe UI", 16, "bold"), 
                 bg="#16213e", fg="#e94560").pack(side=tk.LEFT)
        
        self.status_var = tk.StringVar(value="Connecting...")
        tk.Label(header, textvariable=self.status_var, font=("Segoe UI", 11),
                 bg="#16213e", fg="#ffffff").pack(side=tk.RIGHT)
        
        # Content area
        self.content = tk.Frame(self.frame, bg="#1a1a2e", padx=20, pady=20)
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.columnconfigure(1, weight=1)
        
        # Left panel - Game info and controls
        left_panel = tk.Frame(self.content, bg="#1a1a2e")
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        
        # Round info
        round_frame = tk.Frame(left_panel, bg="#0f3460", padx=15, pady=15)
        round_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.round_var = tk.StringVar(value="Round: -- / --")
        tk.Label(round_frame, textvariable=self.round_var, font=("Segoe UI", 14, "bold"),
                 bg="#0f3460", fg="#ffffff").pack()
        
        self.timer_var = tk.StringVar(value="Time: --")
        tk.Label(round_frame, textvariable=self.timer_var, font=("Segoe UI", 12),
                 bg="#0f3460", fg="#e94560").pack()
        
        self.phase_var = tk.StringVar(value="Waiting for players...")
        tk.Label(round_frame, textvariable=self.phase_var, font=("Segoe UI", 11),
                 bg="#0f3460", fg="#aaaaaa").pack()
        
        # Choice buttons frame
        choice_frame = tk.LabelFrame(left_panel, text="Make Your Choice", 
                                      font=("Segoe UI", 12, "bold"),
                                      bg="#1a1a2e", fg="#ffffff", padx=15, pady=15)
        choice_frame.pack(fill=tk.X, pady=10)
        
        button_style = {"font": ("Segoe UI", 24), "width": 4, "height": 2}
        
        buttons_row = tk.Frame(choice_frame, bg="#1a1a2e")
        buttons_row.pack()
        
        self.rock_btn = tk.Button(buttons_row, text="✊", command=lambda: self.make_choice("rock"),
                                   bg="#e94560", fg="white", **button_style)
        self.rock_btn.pack(side=tk.LEFT, padx=5)
        
        self.paper_btn = tk.Button(buttons_row, text="🖐️", command=lambda: self.make_choice("paper"),
                                    bg="#0f3460", fg="white", **button_style)
        self.paper_btn.pack(side=tk.LEFT, padx=5)
        
        self.scissors_btn = tk.Button(buttons_row, text="✌️", command=lambda: self.make_choice("scissors"),
                                       bg="#533483", fg="white", **button_style)
        self.scissors_btn.pack(side=tk.LEFT, padx=5)
        
        self.choice_label_var = tk.StringVar(value="Select your choice")
        tk.Label(choice_frame, textvariable=self.choice_label_var, font=("Segoe UI", 10),
                 bg="#1a1a2e", fg="#888888").pack(pady=(10, 0))
        
        # Ready button (before game starts)
        self.ready_frame = tk.Frame(left_panel, bg="#1a1a2e")
        self.ready_frame.pack(fill=tk.X, pady=10)
        
        self.ready_btn = tk.Button(self.ready_frame, text="Ready!", font=("Segoe UI", 14, "bold"),
                                    bg="#27ae60", fg="white", command=self.send_ready,
                                    width=15, height=2)
        self.ready_btn.pack()
        
        self.ready_status_var = tk.StringVar(value="Click Ready when you're prepared to play")
        tk.Label(self.ready_frame, textvariable=self.ready_status_var, font=("Segoe UI", 10),
                 bg="#1a1a2e", fg="#888888").pack(pady=(5, 0))
        
        # Right panel - Players list
        right_panel = tk.Frame(self.content, bg="#1a1a2e")
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        
        players_label = tk.Label(right_panel, text="Players", font=("Segoe UI", 14, "bold"),
                                  bg="#1a1a2e", fg="#ffffff")
        players_label.pack(anchor="w")
        
        # Players container with scrolling
        self.players_canvas = tk.Canvas(right_panel, bg="#16213e", highlightthickness=0)
        self.players_canvas.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        
        self.players_frame = tk.Frame(self.players_canvas, bg="#16213e")
        self.players_canvas.create_window((0, 0), window=self.players_frame, anchor="nw")
        
        self.player_widgets: Dict[int, Dict] = {}
        
        # Bottom controls
        controls = tk.Frame(self.frame, bg="#16213e", padx=10, pady=10)
        controls.grid(row=2, column=0, sticky="ew")
        
        ttk.Button(controls, text="Leave Match", command=self.leave_match).pack(side=tk.LEFT)
        ttk.Button(controls, text="Back to Lobby", command=self.back_to_lobby).pack(side=tk.RIGHT)

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
            self.status_var.set("Failed to connect to server")
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
        self.total_rounds = resp.get("totalRounds", 5)
        
        self.status_var.set("Spectating" if self.is_spectator else f"Joined as {self.username}")
        self.sock = s
        
        if self.is_spectator:
            self.ready_frame.pack_forget()
        
        threading.Thread(target=self.recv_loop, daemon=True).start()
        self.root.after(100, self.update_timer)

    def send_ready(self):
        if not self.sock or self.is_spectator or self.is_ready:
            return
        self.is_ready = True
        self.ready_btn.config(state=tk.DISABLED, bg="#888888", text="Ready ✓")
        self.ready_status_var.set("Waiting for other players...")
        try:
            send_json(self.sock, {"type": "READY"})
        except Exception:
            pass

    def make_choice(self, choice: str):
        if not self.sock or self.is_spectator or self.game_over:
            return
        if self.game_phase != "choosing":
            return
        if self.my_choice is not None:
            return  # Already chose
        
        self.my_choice = choice
        self.choice_label_var.set(f"You chose: {CHOICE_DISPLAY[choice]}")
        
        # Highlight selected button
        self.rock_btn.config(bg="#666666" if choice != "rock" else "#27ae60")
        self.paper_btn.config(bg="#666666" if choice != "paper" else "#27ae60")
        self.scissors_btn.config(bg="#666666" if choice != "scissors" else "#27ae60")
        
        try:
            send_json(self.sock, {"type": "CHOICE", "choice": choice})
        except Exception:
            pass

    def recv_loop(self):
        try:
            while True:
                msg = recv_json(self.sock)
                msg_type = msg.get("type")
                
                if msg_type == "STATE":
                    self.root.after(0, self.handle_state, msg)
                elif msg_type == "GAME_START":
                    self.root.after(0, self.handle_game_start, msg)
                elif msg_type == "ROUND_START":
                    self.root.after(0, self.handle_round_start, msg)
                elif msg_type == "ROUND_RESULT":
                    self.root.after(0, self.handle_round_result, msg)
                elif msg_type == "PLAYER_LEFT":
                    self.root.after(0, self.handle_player_left, msg)
                elif msg_type == "GAME_OVER":
                    self.root.after(0, self.handle_game_over, msg)
                    break
        except Exception as e:
            if not self.game_over:
                self.root.after(0, lambda: self.status_var.set("Disconnected"))

    def handle_state(self, msg: Dict):
        self.game_phase = msg.get("phase", "waiting")
        self.current_round = msg.get("round", 0)
        self.total_rounds = msg.get("totalRounds", 5)
        self.time_remaining = msg.get("timeRemaining", 0)
        self.players_data = msg.get("players", [])
        
        # Update round display
        if self.current_round > 0:
            self.round_var.set(f"Round: {self.current_round} / {self.total_rounds}")
        else:
            self.round_var.set("Round: -- / --")
        
        # Update phase display
        phase_text = {
            "waiting": "Waiting for players...",
            "choosing": "Make your choice!",
            "revealing": "Revealing choices...",
            "finished": "Game Over!",
        }
        self.phase_var.set(phase_text.get(self.game_phase, self.game_phase))
        
        # Update timer
        if self.game_phase == "choosing":
            self.timer_var.set(f"Time: {self.time_remaining}s")
        else:
            self.timer_var.set("Time: --")
        
        # Show/hide ready button
        if msg.get("gameStarted"):
            self.ready_frame.pack_forget()
        
        # Update players display
        self.update_players_display()

    def handle_game_start(self, msg: Dict):
        self.player_meta = msg.get("players", [])
        self.total_rounds = msg.get("totalRounds", 5)
        self.ready_frame.pack_forget()
        self.status_var.set("Game started!")

    def handle_round_start(self, msg: Dict):
        self.current_round = msg.get("round", 0)
        self.my_choice = None
        self.choice_label_var.set("Select your choice")
        
        # Reset button colors
        self.rock_btn.config(bg="#e94560")
        self.paper_btn.config(bg="#0f3460")
        self.scissors_btn.config(bg="#533483")
        
        self.round_var.set(f"Round: {self.current_round} / {self.total_rounds}")
        self.phase_var.set("Make your choice!")

    def handle_round_result(self, msg: Dict):
        results = msg.get("results", [])
        
        # Show results in a popup
        result_text = f"Round {msg.get('round', '?')} Results:\n\n"
        for r in results:
            choice_icon = CHOICE_ICONS.get(r.get("choice"), "?")
            result_text += f"{r.get('username', 'Unknown')}: {choice_icon} (+{r.get('roundScore', 0)} pts)\n"
        
        # Update the phase display
        self.phase_var.set("Round results...")

    def handle_player_left(self, msg: Dict):
        """處理玩家中途退出的通知"""
        username = msg.get("username", "A player")
        self.status_var.set(f"⚠️ {username} left the game")
        # 更新玩家列表顯示會透過 STATE 消息自動處理

    def handle_game_over(self, msg: Dict):
        if self.game_over:
            return
        self.game_over = True
        
        summary = msg.get("summary", []) or []
        winner_id = msg.get("winnerId")
        reason = msg.get("reason", "finished")
        
        lines = ["Final Standings:\n"]
        for i, entry in enumerate(summary, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            status = "" if entry.get("alive", True) else " (left)"
            lines.append(f"{medal} {entry.get('username', 'Unknown')}: {entry.get('score', 0)} pts{status}")
        
        # 判斷結束原因
        if reason == "players_left":
            if winner_id is None:
                outcome = "⚠️ Game ended - all players left"
            elif winner_id == self.user_id and not self.is_spectator:
                outcome = "🎉 You win! (opponent left)"
            else:
                winner_name = next((p.get("username") for p in summary if p.get("userId") == winner_id), "Unknown")
                outcome = f"{winner_name} wins! (opponent left)"
        elif winner_id is None:
            outcome = "It's a tie!"
        elif winner_id == self.user_id and not self.is_spectator:
            outcome = "🎉 You win! 🎉"
        else:
            winner_name = next((p.get("username") for p in summary if p.get("userId") == winner_id), "Unknown")
            outcome = f"{winner_name} wins!"
        
        text = outcome + "\n\n" + "\n".join(lines)
        self.status_var.set(outcome)
        
        summary_win = tk.Toplevel(self.root)
        summary_win.title("Game Over")
        summary_win.configure(bg="#1a1a2e")
        
        tk.Label(summary_win, text=text, justify=tk.LEFT, font=("Segoe UI", 12),
                 bg="#1a1a2e", fg="#ffffff").pack(padx=20, pady=20)
        ttk.Button(summary_win, text="Back to Lobby", 
                   command=lambda: self._close_summary(summary_win)).pack(pady=(0, 20))
        summary_win.transient(self.root)
        summary_win.grab_set()
        
        self.cleanup_socket()

    def update_players_display(self):
        """Update the players list display"""
        # Clear existing widgets
        for widget in self.players_frame.winfo_children():
            widget.destroy()
        self.player_widgets.clear()
        
        for i, player in enumerate(self.players_data):
            uid = player.get("userId")
            color = PLAYER_COLORS[i % len(PLAYER_COLORS)]
            
            player_frame = tk.Frame(self.players_frame, bg="#0f3460", padx=10, pady=8)
            player_frame.pack(fill=tk.X, pady=2, padx=5)
            
            # Left side - name and status
            left = tk.Frame(player_frame, bg="#0f3460")
            left.pack(side=tk.LEFT, fill=tk.X, expand=True)
            
            name_text = player.get("username", f"Player {uid}")
            if uid == self.user_id:
                name_text += " (You)"
            
            tk.Label(left, text=name_text, font=("Segoe UI", 11, "bold"),
                     bg="#0f3460", fg=color).pack(anchor="w")
            
            # Status indicators
            status_frame = tk.Frame(left, bg="#0f3460")
            status_frame.pack(anchor="w")
            
            if self.game_phase == "waiting":
                ready_text = "✓ Ready" if player.get("ready") else "○ Not ready"
                ready_color = "#27ae60" if player.get("ready") else "#888888"
                tk.Label(status_frame, text=ready_text, font=("Segoe UI", 9),
                         bg="#0f3460", fg=ready_color).pack(side=tk.LEFT)
            elif self.game_phase == "choosing":
                if player.get("hasChosen"):
                    tk.Label(status_frame, text="✓ Chosen", font=("Segoe UI", 9),
                             bg="#0f3460", fg="#27ae60").pack(side=tk.LEFT)
                else:
                    tk.Label(status_frame, text="○ Choosing...", font=("Segoe UI", 9),
                             bg="#0f3460", fg="#f39c12").pack(side=tk.LEFT)
            elif self.game_phase == "revealing":
                choice = player.get("choice")
                choice_icon = CHOICE_ICONS.get(choice, "?")
                tk.Label(status_frame, text=choice_icon, font=("Segoe UI", 16),
                         bg="#0f3460", fg="#ffffff").pack(side=tk.LEFT)
            
            # Right side - score
            score = player.get("score", 0)
            tk.Label(player_frame, text=f"{score} pts", font=("Segoe UI", 12, "bold"),
                     bg="#0f3460", fg="#e94560").pack(side=tk.RIGHT)
            
            self.player_widgets[uid] = {"frame": player_frame}

    def update_timer(self):
        """Update timer display"""
        if self.game_over:
            return
        
        if self.game_phase == "choosing" and self.time_remaining > 0:
            self.timer_var.set(f"Time: {self.time_remaining}s")
            if self.time_remaining <= 5:
                self.timer_var.set(f"⚠️ Time: {self.time_remaining}s")
        
        self.root.after(500, self.update_timer)

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

    def _close_summary(self, summary_win: tk.Toplevel) -> None:
        if summary_win.winfo_exists():
            summary_win.destroy()
        self.back_to_lobby()

    def run(self):
        self.connect()
        if self.own_root:
            self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="RPS GUI client")
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
