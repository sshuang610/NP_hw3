# NP HW3 – Game Store System

## 環境需求

- Python 3.10+
- Tkinter, SQLite3 (Python 標準函式庫)

## 部署檔案架構(可以直接從codebase資料夾下載)

### Server 端
```
server/
  db_server.py
  developer_server.py
  lobby_server.py
  storage/          (自動建立)
  runtime/          (自動建立)
common/
  lp.py
store.sqlite3       (自動建立)
```

### Developer Client 端
```
developer_client/
  gui.py
common/
  lp.py
game_templates/     (上傳用的遊戲範例)
  connect4_cli/
  tetris_gui/
  rps_gui/
```

### Player Client 端
```
player_client/
  gui.py
  downloads/        (自動建立，存放下載的遊戲)
  plugins/          (自動建立)
common/
  lp.py
```

## 操作流程：從上架到遊玩

### 1. 啟動伺服器

若三個伺服器在同一台機器：
```bash
python -m server.db_server --port 23000
python -m server.developer_server --port 23001
python -m server.lobby_server --port 23002
```

若部署在不同server (假設 DB Server 在 140.113.17.13)：
```bash
# DB Server (140.113.17.13)
python -m server.db_server --host 0.0.0.0 --port 23000

# Developer Server (指定 DB Server 位置)
python -m server.developer_server --host 0.0.0.0 --port 23001 --db-host 140.113.17.13 --db-port 23000

# Lobby Server (指定 DB Server 位置，以及對外公開的 IP)
python -m server.lobby_server --host 0.0.0.0 --port 23002 --db-host 140.113.17.13 --db-port 23000 --public-host 140.113.17.11
```

### 2. 開發者上架遊戲
```powershell
python -m developer_client.gui
```
1. 輸入lobby server的host及port，輸入帳號密碼，註冊並登入
2. 點擊 Create Game，填寫 Title / Min Players / Max Players
3. 選擇遊戲，點擊 Upload Version，選擇 `game_templates/` 下的目錄
4. 點擊 Publish 發布遊戲

### 3. 玩家下載遊戲
```powershell
python -m player_client.gui
```
1. 輸入lobby server的host及port，輸入帳號密碼，註冊並登入
2. Store 分頁：選擇遊戲，點擊 Download

### 4. 建立房間並遊玩
1. Rooms 分頁：點擊 Create Room，選擇遊戲
2. 另開一個 Player Client，註冊第二位玩家，下載遊戲後 Join Room
3. 房主點擊 Start Game 啟動遊戲

## 遊戲模板

| 模板 | 類型 | 人數 | 說明 |
|------|------|------|------|
| connect4_cli | CLI | 2 | 連四棋，輸入 1-7 放置棋子 |
| tetris_gui | GUI | 2 | 俄羅斯方塊，方向鍵控制 |
| rps_gui | GUI | 2-8 | 猜拳，點擊選擇石頭/布/剪刀 |


## 技術細節

- 網路協定: Length-Prefixed JSON (4 bytes header + UTF-8 JSON)
- 連接埠: DB 23000 / Developer 23001 / Lobby 23002
- 資料庫: SQLite3 (store.sqlite3)
