"""
Power Connect Four Client for np_hw3 platform
Adapted from HW1 - includes lift skill mechanic
"""
import os
import socket
import sys
import time
from typing import Dict, List, Optional

from lp import recv_json, send_json

SYMBOLS = {0: ".", 1: "X", 2: "O", 3: "A", 4: "B"}


def render_board(board: List[List[int]]) -> str:
    rows = []
    for row in board:
        cells = " ".join(SYMBOLS.get(cell, "?") for cell in row)
        rows.append(f"| {cells} |")
    footer = "  " + " ".join(str(idx) for idx in range(len(board[0])))
    return "\n".join(rows + [footer])


def main() -> None:
    host = os.getenv("GAME_HOST", "127.0.0.1")
    port = int(os.getenv("GAME_PORT", "31000"))
    room_token = os.getenv("GAME_ROOM_TOKEN", "")
    player_id = int(os.getenv("PLAYER_ID", "0"))
    username = os.getenv("PLAYER_USERNAME", f"Player{player_id}")

    print(f"Connecting to Power Connect Four server {host}:{port} as {username}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    for i in range(10):
        try:
            sock.connect((host, port))
            break
        except ConnectionRefusedError:
            print(f"Connection failed, retrying ({i+1}/10)...")
            time.sleep(0.5)
    else:
        print("Could not connect to server.")
        return

    last_board_sig: Optional[tuple] = None

    def maybe_render(current: List[List[int]], force: bool = False) -> None:
        nonlocal last_board_sig
        if current is None:
            return
        signature = tuple(tuple(row) for row in current)
        if force or signature != last_board_sig:
            print(render_board(current))
            last_board_sig = signature

    with sock:
        send_json(
            sock,
            {
                "type": "HELLO",
                "playerId": player_id,
                "username": username,
                "roomToken": room_token,
            },
        )
        slot = None
        symbol = None
        board: List[List[int]] = []
        skill_available = True
        liftable_columns: List[int] = []
        
        while True:
            msg = recv_json(sock)
            mtype = msg.get("type")
            if mtype == "WELCOME":
                slot = msg.get("slot")
                symbol = msg.get("symbol")
                board = msg.get("board", board)
                skill_available = msg.get("skillAvailable", True)
                print("Connected! Assigned slot", slot, "symbol", symbol)
                print("Game: Power Connect Four (with Lift skill)")
                maybe_render(board, force=True)
            elif mtype == "READY":
                board = msg.get("board", board)
                names = [p.get("displayName", f"Player {p.get('slot', '?')}") for p in msg.get("players", [])]
                print("Players ready:", ", ".join(names))
                maybe_render(board)
            elif mtype == "BOARD_STATE":
                board = msg.get("board", board)
                last = msg.get("lastMove")
                if last:
                    mover = last.get("slot")
                    kind = last.get("kind", "drop")
                    data = last.get("data", {})
                    if mover is not None:
                        if kind == "lift":
                            src = data.get("source", "?")
                            tgt = data.get("target", "?")
                            if mover == slot:
                                print(f"You used Lift skill: lifted from column {src}, dropped in column {tgt}.")
                            else:
                                print(f"Opponent used Lift skill: lifted from column {src}, dropped in column {tgt}.")
                        else:
                            column = data.get("column", "?")
                            if mover == slot:
                                print(f"You placed a piece in column {column}.")
                            else:
                                print(f"Opponent placed in column {column}.")
                    maybe_render(board)
            elif mtype == "YOUR_TURN":
                board = msg.get("board", board)
                skill_available = msg.get("skillAvailable", False)
                liftable_columns = msg.get("liftableColumns", [])
                maybe_render(board)
                max_col = len(board[0]) - 1 if board and board[0] else msg.get("columnCount", 7) - 1
                
                # Show skill status
                skill_info = "available" if skill_available else "used"
                print(f"Your turn. Lift skill is {skill_info}.")
                if skill_available and liftable_columns:
                    print(f"Liftable columns (bottom has your piece): {liftable_columns}")
                
                while True:
                    if skill_available and liftable_columns:
                        raw = input(f"Choose action [d=drop, l=lift, q=quit]: ").strip().lower()
                    else:
                        raw = input(f"Choose a column (0-{max_col}) or 'q' to quit: ").strip().lower()
                        if raw and raw[0].isdigit():
                            raw = 'd' + raw  # Treat as drop
                    
                    if raw in ('q', 'quit'):
                        print("Forfeiting the game...")
                        print("You lose this match. Returning to lobby...")
                        sock.close()
                        return
                    
                    if raw.startswith('d') or raw.startswith('drop'):
                        # Drop action
                        col_str = raw[1:].strip() if raw.startswith('d') else raw[4:].strip()
                        if not col_str:
                            col_str = input(f"Drop column (0-{max_col}): ").strip()
                        try:
                            column = int(col_str)
                            if 0 <= column <= max_col:
                                send_json(sock, {"type": "MOVE", "kind": "drop", "column": column})
                                break
                            else:
                                print("Column out of range.")
                        except ValueError:
                            print("Please enter a valid number.")
                    
                    elif raw.startswith('l') or raw.startswith('lift'):
                        if not skill_available:
                            print("Lift skill already used this game.")
                            continue
                        if not liftable_columns:
                            print("No columns available to lift from (need your piece at bottom).")
                            continue
                        
                        # Ask for source column
                        src_str = input(f"Lift from column (available: {liftable_columns}): ").strip()
                        try:
                            source = int(src_str)
                            if source not in liftable_columns:
                                print("Invalid lift column.")
                                continue
                        except ValueError:
                            print("Please enter a valid number.")
                            continue
                        
                        # Ask for target column
                        tgt_str = input(f"Re-drop into column (0-{max_col}): ").strip()
                        try:
                            target = int(tgt_str)
                            if 0 <= target <= max_col:
                                send_json(sock, {"type": "MOVE", "kind": "lift", "source": source, "target": target})
                                skill_available = False
                                break
                            else:
                                print("Column out of range.")
                        except ValueError:
                            print("Please enter a valid number.")
                    else:
                        # Try to parse as column number directly
                        try:
                            column = int(raw)
                            if 0 <= column <= max_col:
                                send_json(sock, {"type": "MOVE", "kind": "drop", "column": column})
                                break
                            else:
                                print("Column out of range.")
                        except ValueError:
                            print("Invalid choice. Enter a column number or 'd'/'l' for actions.")
                            
            elif mtype == "INVALID_MOVE":
                reason = msg.get("message", "Invalid move")
                print(f"Server rejected move: {reason}")
            elif mtype == "GAME_OVER":
                board = msg.get("board", board)
                winner = msg.get("winnerSlot")
                reason = msg.get("reason")
                maybe_render(board, force=True)
                if winner is None:
                    if reason == "draw":
                        print("Game finished in a draw!")
                    else:
                        print("Game ended without a winner (", reason, ")")
                elif winner == slot:
                    print("Congratulations, you win!")
                else:
                    print("You lose this round. GG!")
                print("Returning to lobby and refreshing room status...")
                break
            else:
                print("Received:", msg)
    print("Connection closed.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")
        sys.exit(1)
