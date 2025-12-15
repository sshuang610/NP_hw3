"""
NP HW3 系統啟動腳本 (本機測試用)

此腳本會在本機依序啟動所有伺服器，方便測試:
1. DB Server      (port 23000) - 資料庫伺服器
2. Lobby Server   (port 23002) - 玩家大廳 API
3. Developer Server (port 23001) - 開發者 API

系統架構:
+-----------------+
|   DB Server     |  <-- 資料庫層 (SQLite)
|   (port 23000)  |
+--------+--------+
         |
    +----+----+
    |         |
+---+---+ +---+---+
| Lobby | | Dev   |  <-- API 層
| Server| | Server|
| 23002 | | 23001 |
+---+---+ +---+---+
    |         |
+---+---+ +---+---+
| Player| | Dev   |  <-- 客戶端
| Client| | Client|
+-------+ +-------+

注意: 此腳本僅供本機測試使用
實際 Demo 時，各伺服器應在不同機器上獨立執行

使用方式:
    python run_system.py

啟動後可以運行客戶端:
    python -m developer_client.gui  # 開發者控制台
    python -m player_client.gui     # 玩家大廳

按 Ctrl+C 停止所有伺服器
"""
import subprocess
import sys
import time
import os


def main():
    """啟動所有伺服器"""
    print("Starting NP HW3 System (Local Test Mode)...")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_proc = lobby_proc = dev_proc = None
    
    # 啟動 DB Server
    print("Launching DB Server (port 23000)...")
    db_proc = subprocess.Popen([sys.executable, "-m", "server.db_server"], cwd=base_dir)
    time.sleep(1)
    
    # 啟動 Lobby Server
    print("Launching Lobby Server (port 23002)...")
    lobby_proc = subprocess.Popen([sys.executable, "-m", "server.lobby_server"], cwd=base_dir)
    time.sleep(1)
    
    # 啟動 Developer Server
    print("Launching Developer Server (port 23001)...")
    dev_proc = subprocess.Popen([sys.executable, "-m", "server.developer_server"], cwd=base_dir)
    time.sleep(1)
    
    print("\n" + "="*50)
    print("All servers are running.")
    print("="*50)
    print("\nRun clients in separate terminals:")
    print("  python -m developer_client.gui")
    print("  python -m player_client.gui")
    print("\nPress Ctrl+C to stop all servers.")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping servers...")
        for proc in (dev_proc, lobby_proc, db_proc):
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    pass
        print("Done.")


if __name__ == "__main__":
    main()
