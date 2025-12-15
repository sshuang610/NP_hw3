# Connect4 CLI Template

This template packages a two-player Connect Four experience ready for the NP HW3 game store pipeline. It ships with a menu-driven client, a lightweight TCP game server, and a manifest describing the metadata required by the developer portal.

## Contents

- `manifest.json` – metadata consumed by the developer client when uploading.
- `server.py` – runtime started by the lobby to host a single match (reads `GAME_*` environment variables).
- `client.py` – CLI client launched for each player (reads `GAME_HOST`, `GAME_PORT`, etc.).
- `lp.py` – minimal length-prefixed JSON helper shared by server/client.

## Expected Environment Variables

The lobby passes the following variables automatically:

- `GAME_SERVER_HOST` / `GAME_SERVER_PORT` – bind address for the game server.
- `GAME_ROOM_ID` / `GAME_ROOM_TOKEN` – identifiers for the current lobby room.
- `ROOM_PLAYERS` – JSON payload describing players assigned to the room.
- `GAME_HOST` / `GAME_PORT` – remote endpoint the client should connect to.
- `PLAYER_ID`, `PLAYER_USERNAME`, `PLAYER_DISPLAY_NAME` – identity for the local player.

## Local Testing

```bash
# Terminal 1 – start the server
export GAME_SERVER_HOST=127.0.0.1
export GAME_SERVER_PORT=31000
export GAME_ROOM_TOKEN=test
export ROOM_PLAYERS='[{"playerId": 1, "displayName": "Alice"}, {"playerId": 2, "displayName": "Bob"}]'
python server.py

# Terminal 2 – player one
export GAME_HOST=127.0.0.1
export GAME_PORT=31000
export GAME_ROOM_TOKEN=test
export PLAYER_ID=1
export PLAYER_DISPLAY_NAME="Alice"
python client.py

# Terminal 3 – player two
export GAME_HOST=127.0.0.1
export GAME_PORT=31000
export GAME_ROOM_TOKEN=test
export PLAYER_ID=2
export PLAYER_DISPLAY_NAME="Bob"
python client.py
```

## Packaging Tips

1. Run `python -m tools.create_game_template --template connect4_cli --dest ./games/my_connect4` to scaffold a new copy.
2. Bump the `version` field in `manifest.json` before uploading a new release through the developer console.
3. Zip the folder (or let the developer client handle packaging) and upload via the GUI.
