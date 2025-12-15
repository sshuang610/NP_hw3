# Game Templates

此目錄包含可直接上傳的遊戲範本。每個範本包含:

- `manifest.json` - 遊戲設定 (入口點、玩家數量等)
- `server.py` - 遊戲伺服器
- `client.py` - 遊戲客戶端
- `lp.py` - 網路協定模組
- `README.md` - 遊戲說明文件

## 可用範本

### connect4_cli/ (關卡A - 雙人 CLI)
- **玩家數**: 2 人
- **介面**: CLI (命令列)
- **說明**: 經典連四棋遊戲

### tetris_gui/ (關卡B - 雙人 GUI)
- **玩家數**: 2 人
- **介面**: GUI (Tkinter)
- **說明**: 雙人對戰 Tetris

### rps_gui/ (關卡C - 多人 GUI)
- **玩家數**: 2-8 人
- **介面**: GUI (Tkinter)
- **說明**: 多人猜拳大亂鬥，支援 2-8 人同時對戰

## 使用方式

1. 在開發者客戶端建立遊戲
2. 選擇 "Upload Version"
3. 選擇此目錄下的任一遊戲資料夾
4. 發布遊戲 (設定狀態為 published)
