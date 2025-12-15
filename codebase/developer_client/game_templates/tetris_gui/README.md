# Tetris GUI Template

This template ports the HW2 Tetris duel into the NP HW3 store pipeline. It bundles a Tkinter-based client, a fully featured asynchronous game server, and metadata describing how the lobby should start each side.

## Contents

- `manifest.json` – Upload metadata (entrypoints, player counts, description).
- `server.py` – Launches a headless Tetris duel server that honours the lobby environment variables.
- `client.py` – Tkinter GUI client that connects to the running game server and renders both boards.
- `lp.py` – Length-prefixed JSON helper used by both endpoints.

## Environment Variables

When launched through the lobby, the following variables are supplied automatically.

- `GAME_SERVER_HOST`, `GAME_SERVER_PORT` – Bind address for the server process.
- `GAME_ROOM_ID`, `GAME_ROOM_TOKEN` – Identifiers for the lobby room.
- `ROOM_PLAYERS` – JSON array with player identities (id, username, displayName).
- `ROOM_METADATA` – Optional JSON for extra rules (e.g., `{"mode": "timed", "lineTarget": 30}`).
- `GAME_HOST`, `GAME_PORT` – Remote endpoint the client should connect to.
- `PLAYER_ID`, `PLAYER_USERNAME`, `PLAYER_DISPLAY_NAME` – Identity for the client process.

## Local Testing

```bash
# Terminal 1 – start server
export GAME_SERVER_HOST=127.0.0.1
export GAME_SERVER_PORT=32000
export GAME_ROOM_ID=1
export GAME_ROOM_TOKEN=secret
export ROOM_PLAYERS='[{"playerId": 1, "displayName": "Alice"}, {"playerId": 2, "displayName": "Bob"}]'
python server.py

# Terminal 2 – player Alice
export GAME_HOST=127.0.0.1
export GAME_PORT=32000
export GAME_ROOM_TOKEN=secret
export PLAYER_ID=1
export PLAYER_DISPLAY_NAME="Alice"
python client.py

# Terminal 3 – player Bob
export GAME_HOST=127.0.0.1
export GAME_PORT=32000
export GAME_ROOM_TOKEN=secret
export PLAYER_ID=2
export PLAYER_DISPLAY_NAME="Bob"
python client.py
```

## Packaging Tips

1. Scaffold new copies via `python -m tools.create_game_template --template tetris_gui --dest ./games/my_tetris`.
2. Update `manifest.json` (name/version/changelog) before uploading.
3. Package the directory as a zip (handled automatically by the developer console).
