"""
Rock-Paper-Scissors Multiplayer Game Server
Supports 3-8 players in simultaneous rounds
"""
import argparse
import json
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from lp import recv_json, send_json

# Game constants
CHOICES = ["rock", "paper", "scissors"]
CHOICE_BEATS = {
    "rock": "scissors",
    "paper": "rock",
    "scissors": "paper",
}

# Game settings
MIN_PLAYERS = 2
MAX_PLAYERS = 8
ROUND_TIMEOUT_SEC = 15  # Time limit for each round
TOTAL_ROUNDS = 5  # Number of rounds to play


@dataclass
class Player:
    """Player state"""
    player_id: int
    username: str
    score: int = 0
    current_choice: Optional[str] = None
    alive: bool = True
    disconnected: bool = False
    ready: bool = False


class RPSServer:
    def __init__(self, host: str, port: int, room_id: int, room_token: str):
        self.host = host
        self.port = port
        self.room_id = room_id
        self.room_token = room_token
        
        self.clients: Dict[socket.socket, Dict] = {}
        self.players: Dict[int, Player] = {}
        self.client_by_player: Dict[int, socket.socket] = {}
        
        self.expected_players = MIN_PLAYERS
        self.current_round = 0
        self.total_rounds = TOTAL_ROUNDS
        self.round_timeout = ROUND_TIMEOUT_SEC
        self.round_start_time: float = 0
        self.game_started = False
        self.game_phase = "waiting"  # waiting, choosing, revealing, finished
        
        self.running = True
        self.lock = threading.Lock()
        self.winner_id: Optional[int] = None
        self.end_reason: Optional[str] = None
        self.player_meta: List[Dict] = []
        self.server_socket: Optional[socket.socket] = None

    def broadcast(self, msg: Dict, exclude: Optional[socket.socket] = None):
        """Broadcast message to all connected clients"""
        with self.lock:
            targets = list(self.clients.keys())
        for conn in targets:
            if conn == exclude:
                continue
            try:
                send_json(conn, msg)
            except Exception:
                pass

    def broadcast_state(self):
        """Broadcast current game state to all clients"""
        with self.lock:
            state = self.get_game_state()
        self.broadcast(state)

    def get_game_state(self) -> Dict:
        """Get current game state for broadcasting"""
        players_info = []
        for p in self.players.values():
            info = {
                "userId": p.player_id,
                "username": p.username,
                "score": p.score,
                "alive": p.alive,
                "ready": p.ready,
                "hasChosen": p.current_choice is not None,
            }
            # Only reveal choice if in revealing phase
            if self.game_phase == "revealing":
                info["choice"] = p.current_choice
            players_info.append(info)
        
        time_remaining = 0
        if self.game_phase == "choosing" and self.round_start_time > 0:
            elapsed = time.time() - self.round_start_time
            time_remaining = max(0, self.round_timeout - int(elapsed))
        
        return {
            "type": "STATE",
            "phase": self.game_phase,
            "round": self.current_round,
            "totalRounds": self.total_rounds,
            "players": players_info,
            "timeRemaining": time_remaining,
            "gameStarted": self.game_started,
        }

    def start_game(self):
        """Start the game when enough players are ready"""
        with self.lock:
            if self.game_started:
                return
            if len(self.players) < MIN_PLAYERS:
                return
            self.game_started = True
            self.current_round = 0
        
        self.broadcast({
            "type": "GAME_START",
            "players": self.player_meta,
            "totalRounds": self.total_rounds,
        })
        
        time.sleep(1)
        self.start_round()

    def start_round(self):
        """Start a new round"""
        with self.lock:
            self.current_round += 1
            if self.current_round > self.total_rounds:
                self.finish_game()
                return
            
            # Reset choices for all alive players
            for p in self.players.values():
                if p.alive:
                    p.current_choice = None
            
            self.game_phase = "choosing"
            self.round_start_time = time.time()
        
        self.broadcast({
            "type": "ROUND_START",
            "round": self.current_round,
            "timeout": self.round_timeout,
        })
        self.broadcast_state()

    def check_round_complete(self) -> bool:
        """Check if all alive players have made their choice"""
        with self.lock:
            alive_players = [p for p in self.players.values() if p.alive and not p.disconnected]
            if not alive_players:
                return True
            return all(p.current_choice is not None for p in alive_players)

    def process_round(self):
        """Process the round results"""
        with self.lock:
            self.game_phase = "revealing"
            alive_players = [p for p in self.players.values() if p.alive and not p.disconnected]
            
            # Auto-assign random choice for players who didn't choose
            import random
            for p in alive_players:
                if p.current_choice is None:
                    p.current_choice = random.choice(CHOICES)
            
            # Get all choices
            choices = {p.player_id: p.current_choice for p in alive_players}
            
            # Determine round results
            # For multiplayer: count wins for each player against all others
            round_scores = {pid: 0 for pid in choices}
            
            player_ids = list(choices.keys())
            for i, p1_id in enumerate(player_ids):
                for p2_id in player_ids[i+1:]:
                    c1 = choices[p1_id]
                    c2 = choices[p2_id]
                    
                    if c1 == c2:
                        # Tie - no points
                        pass
                    elif CHOICE_BEATS[c1] == c2:
                        # p1 wins
                        round_scores[p1_id] += 1
                    else:
                        # p2 wins
                        round_scores[p2_id] += 1
            
            # Update player scores
            for pid, wins in round_scores.items():
                self.players[pid].score += wins
            
            results = []
            for p in alive_players:
                results.append({
                    "userId": p.player_id,
                    "username": p.username,
                    "choice": p.current_choice,
                    "roundScore": round_scores.get(p.player_id, 0),
                    "totalScore": p.score,
                })
        
        self.broadcast({
            "type": "ROUND_RESULT",
            "round": self.current_round,
            "results": results,
        })
        self.broadcast_state()

    def finish_game(self):
        """Finish the game and determine winner"""
        with self.lock:
            self.game_phase = "finished"
            self.running = False
            
            # Determine winner (highest score)
            alive_players = [p for p in self.players.values() if p.alive and not p.disconnected]
            if alive_players:
                max_score = max(p.score for p in alive_players)
                winners = [p for p in alive_players if p.score == max_score]
                if len(winners) == 1:
                    self.winner_id = winners[0].player_id
                else:
                    # Tie - no single winner
                    self.winner_id = None
            else:
                self.winner_id = None
            
            self.end_reason = "game_complete"
        
        self.finish_match()

    def game_loop(self):
        """Main game loop"""
        while self.running:
            if not self.game_started:
                # Check if we have enough players to start
                with self.lock:
                    ready_count = sum(1 for p in self.players.values() if p.ready and not p.disconnected)
                    total_count = sum(1 for p in self.players.values() if not p.disconnected)
                
                if total_count >= MIN_PLAYERS and ready_count == total_count:
                    self.start_game()
                else:
                    time.sleep(0.2)
                continue
            
            if self.game_phase == "choosing":
                # Check if round is complete or timeout
                elapsed = time.time() - self.round_start_time
                if self.check_round_complete() or elapsed >= self.round_timeout:
                    self.process_round()
                    time.sleep(3)  # Show results for 3 seconds
                    
                    if self.current_round < self.total_rounds:
                        self.start_round()
                    else:
                        self.finish_game()
            
            time.sleep(0.1)

    def state_broadcast_loop(self):
        """Periodically broadcast game state"""
        while self.running:
            self.broadcast_state()
            time.sleep(0.5)

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
                is_player = not spectate and len(self.players) < MAX_PLAYERS and not self.game_started
                if is_player:
                    pl = Player(player_id=player_id, username=username)
                    self.players[player_id] = pl
                    self.clients[conn] = {"playerId": player_id, "spectator": False}
                    self.client_by_player[player_id] = conn
                    self.player_meta.append({
                        "userId": player_id,
                        "playerId": player_id,
                        "username": username,
                    })
                    role = "PLAYER"
                else:
                    role = "SPEC"
                    self.clients[conn] = {"playerId": player_id, "spectator": True}
            
            send_json(conn, {
                "type": "WELCOME",
                "role": role,
                "playerId": player_id,
                "minPlayers": MIN_PLAYERS,
                "maxPlayers": MAX_PLAYERS,
                "totalRounds": self.total_rounds,
                "roundTimeout": self.round_timeout,
                "players": self.player_meta,
            })
            
            self.broadcast_state()
            
            if role == "SPEC":
                while self.running:
                    with self.lock:
                        if conn not in self.clients:
                            break
                    time.sleep(0.3)
                return
            
            # Handle player messages
            while self.running:
                msg = recv_json(conn)
                msg_type = msg.get("type")
                
                if msg_type == "READY":
                    with self.lock:
                        p = self.players.get(player_id)
                        if p:
                            p.ready = True
                    self.broadcast_state()
                
                elif msg_type == "CHOICE":
                    choice = msg.get("choice")
                    if choice in CHOICES:
                        with self.lock:
                            p = self.players.get(player_id)
                            if p and p.alive and self.game_phase == "choosing":
                                p.current_choice = choice
                        self.broadcast_state()
        
        except Exception as e:
            print(f"[RPS] Client error: {e}")
        finally:
            self.remove_connection(conn, player_id)

    def remove_connection(self, conn: socket.socket, player_id: Optional[int] = None):
        """處理玩家中途退出"""
        left_player_name = None
        with self.lock:
            info = self.clients.pop(conn, None)
            if info and not info.get("spectator"):
                uid = info.get("playerId")
                self.client_by_player.pop(uid, None)
                player = self.players.get(uid)
                if player:
                    player.disconnected = True
                    player.alive = False
                    left_player_name = player.username
                
                # Check if game should end due to disconnections
                alive_count = sum(1 for p in self.players.values() if p.alive and not p.disconnected)
                if self.game_started and alive_count < 2:
                    if alive_count == 1:
                        winner = next(p for p in self.players.values() if p.alive and not p.disconnected)
                        self.winner_id = winner.player_id
                    else:
                        self.winner_id = None
                    self.end_reason = "players_left"
                    self.running = False
        
        try:
            conn.close()
        except Exception:
            pass
        
        # 通知其他玩家有人離開
        if left_player_name:
            self.broadcast({
                "type": "PLAYER_LEFT",
                "username": left_player_name,
                "playerId": player_id,
            })
        
        self.broadcast_state()

    def finish_match(self):
        """Send GAME_OVER to all clients"""
        self.running = False
        if self.end_reason is None:
            self.end_reason = "finished"
        
        with self.lock:
            results = sorted(
                [
                    {"userId": p.player_id, "username": p.username, "score": p.score, "alive": p.alive}
                    for p in self.players.values()
                ],
                key=lambda x: x["score"],
                reverse=True
            )
        
        pkt = {
            "type": "GAME_OVER",
            "summary": results,
            "winnerId": self.winner_id,
            "reason": self.end_reason,
            "players": self.player_meta,
        }
        
        for conn in list(self.clients.keys()):
            try:
                send_json(conn, pkt)
            except Exception:
                pass

    def serve(self):
        print(f"[RPS] Starting server on {self.host}:{self.port}")
        print(f"[RPS] Room {self.room_id}, Min players: {MIN_PLAYERS}, Max players: {MAX_PLAYERS}")
        
        threading.Thread(target=self.game_loop, daemon=True).start()
        threading.Thread(target=self.state_broadcast_loop, daemon=True).start()
        
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.settimeout(1.0)
        server.bind((self.host, self.port))
        server.listen()
        self.server_socket = server
        print(f"[RPS] Listening on {self.host}:{self.port}")
        
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
        
        print(f"[RPS] Room {self.room_id} server shutting down")
        time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("GAME_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("GAME_SERVER_PORT", "0")))
    args = parser.parse_args()
    
    room_id = int(os.getenv("GAME_ROOM_ID", "0"))
    room_token = os.getenv("GAME_ROOM_TOKEN", "")
    
    if not room_id or not room_token:
        print("[RPS] Error: Missing GAME_ROOM_ID or GAME_ROOM_TOKEN environment variables")
        return
    
    server = RPSServer(args.host, args.port, room_id, room_token)
    server.serve()


if __name__ == "__main__":
    main()
