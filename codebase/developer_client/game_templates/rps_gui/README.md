# Rock Paper Scissors - Multiplayer GUI Game

多人剪刀石頭布 GUI 遊戲模板，支援 2-8 位玩家同時遊玩。

## 遊戲特色

- **多人對戰**: 支援 2-8 位玩家同時進行遊戲
- **同步機制**: 所有玩家同時做出選擇，確保公平性
- **計時系統**: 每回合有 15 秒的選擇時間
- **即時狀態更新**: 所有玩家可以看到其他人是否已做出選擇
- **積分系統**: 每回合與所有其他玩家進行比較，計算勝場數作為積分

## 遊戲規則

1. **等待階段**: 所有玩家加入後點擊 "Ready!" 準備開始
2. **選擇階段**: 每回合有 15 秒時間選擇剪刀、石頭或布
3. **揭曉階段**: 所有人的選擇同時揭曉，計算該回合積分
4. **獲勝條件**: 進行 5 回合後，總積分最高的玩家獲勝

### 計分方式

每回合，你的選擇會與所有其他玩家進行比較：
- 贏一場: +1 分
- 平局: +0 分
- 輸一場: +0 分

例如在 4 人遊戲中，如果你選擇石頭：
- 對手 A 選布 → 你輸 (0分)
- 對手 B 選剪刀 → 你贏 (+1分)
- 對手 C 選石頭 → 平局 (0分)
- 本回合總得分: 1 分

## 操作說明

### 玩家

1. 加入遊戲房間後，點擊 **Ready!** 按鈕
2. 等待其他玩家準備就緒
3. 遊戲開始後，在時限內點擊 🪨 (石頭)、📄 (布) 或 ✂️ (剪刀)
4. 等待所有玩家選擇完畢或時間到
5. 查看回合結果，準備下一回合

### 觀戰者

觀戰者可以看到所有玩家的狀態，但無法參與遊戲。

## 技術規格

- **最少玩家**: 2 人
- **最多玩家**: 8 人
- **回合數**: 5 回合
- **每回合時限**: 15 秒
- **客戶端模式**: GUI (Tkinter)

## 環境變數

伺服器端：
- `GAME_SERVER_HOST`: 伺服器主機 (預設: 127.0.0.1)
- `GAME_SERVER_PORT`: 伺服器埠號
- `GAME_ROOM_ID`: 房間 ID
- `GAME_ROOM_TOKEN`: 房間驗證 Token

客戶端：
- `GAME_HOST`: 遊戲伺服器主機
- `GAME_PORT`: 遊戲伺服器埠號
- `GAME_ROOM_ID`: 房間 ID
- `PLAYER_ID`: 玩家 ID
- `PLAYER_USERNAME`: 玩家名稱
- `GAME_ROOM_TOKEN`: 房間驗證 Token

## 檔案結構

```
rps_gui/
├── manifest.json    # 遊戲配置
├── server.py        # 遊戲伺服器
├── client.py        # GUI 客戶端
├── lp.py            # 網路通訊協定
└── README.md        # 說明文件
```

## 通訊協定

### 客戶端 → 伺服器

```json
// 連線
{"type": "HELLO", "roomId": 1, "playerId": 1, "username": "player1", "roomToken": "xxx"}

// 準備就緒
{"type": "READY"}

// 做出選擇
{"type": "CHOICE", "choice": "rock|paper|scissors"}
```

### 伺服器 → 客戶端

```json
// 歡迎訊息
{"type": "WELCOME", "role": "PLAYER", "playerId": 1, ...}

// 遊戲狀態
{"type": "STATE", "phase": "waiting|choosing|revealing|finished", "round": 1, "players": [...]}

// 遊戲開始
{"type": "GAME_START", "players": [...], "totalRounds": 5}

// 回合開始
{"type": "ROUND_START", "round": 1, "timeout": 15}

// 回合結果
{"type": "ROUND_RESULT", "round": 1, "results": [{...}]}

// 遊戲結束
{"type": "GAME_OVER", "summary": [...], "winnerId": 1}
```
