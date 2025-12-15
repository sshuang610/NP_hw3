#!/usr/bin/env python3
"""
Plugin Registration Tool
用於將 Plugin 打包並註冊到系統中
"""
import json
import hashlib
import zipfile
import socket
import argparse
from pathlib import Path
from typing import Any, Dict

# 添加 common 路徑
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from common.lp import send_json, recv_json

PLUGINS_SOURCE = Path(__file__).parent / "storage" / "plugins"
PLUGINS_STORAGE = Path(__file__).parent / "storage" / "plugins"

def package_plugin(plugin_dir: Path) -> tuple[bytes, Dict[str, Any]]:
    """
    打包 Plugin 目錄為 zip 並返回 metadata
    """
    # 讀取 plugin.json
    plugin_json = plugin_dir / "plugin.json"
    if not plugin_json.exists():
        raise FileNotFoundError(f"plugin.json not found in {plugin_dir}")
    
    metadata = json.loads(plugin_json.read_text(encoding="utf-8"))
    
    # 創建 zip 包
    zip_path = plugin_dir / f"{metadata['slug']}.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file in plugin_dir.iterdir():
            if file.suffix == '.zip':
                continue
            if file.is_file():
                zf.write(file, file.name)
    
    # 計算 hash
    zip_data = zip_path.read_bytes()
    sha256 = hashlib.sha256(zip_data).hexdigest()
    
    return zip_data, {
        **metadata,
        "package_path": str(zip_path.resolve()),
        "package_size": len(zip_data),
        "package_sha256": sha256,
    }

def register_plugin_to_db(db_host: str, db_port: int, plugin_info: Dict[str, Any]) -> bool:
    """
    向 DB Server 註冊 Plugin
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((db_host, db_port))
        
        send_json(sock, {
            "entity": "Plugin",
            "action": "upsert",
            "data": {
                "slug": plugin_info["slug"],
                "name": plugin_info["name"],
                "description": plugin_info.get("description", ""),
                "latestVersion": plugin_info["version"],
                "packagePath": plugin_info["package_path"],
                "packageSize": plugin_info["package_size"],
                "packageSha256": plugin_info["package_sha256"],
            }
        })
        
        resp = recv_json(sock)
        sock.close()
        return resp.get("ok", False)
    except Exception as e:
        print(f"Failed to register plugin: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Register plugins to the system")
    parser.add_argument("--db-host", default="127.0.0.1", help="DB Server host")
    parser.add_argument("--db-port", type=int, default=23000, help="DB Server port")
    parser.add_argument("--plugin", help="Specific plugin slug to register (default: all)")
    args = parser.parse_args()
    
    if not PLUGINS_SOURCE.exists():
        print(f"Plugins source directory not found: {PLUGINS_SOURCE}")
        return
    
    plugins_to_process = []
    
    if args.plugin:
        plugin_dir = PLUGINS_SOURCE / args.plugin
        if plugin_dir.exists():
            plugins_to_process.append(plugin_dir)
        else:
            print(f"Plugin not found: {args.plugin}")
            return
    else:
        plugins_to_process = [d for d in PLUGINS_SOURCE.iterdir() if d.is_dir()]
    
    for plugin_dir in plugins_to_process:
        try:
            print(f"Processing plugin: {plugin_dir.name}")
            _, plugin_info = package_plugin(plugin_dir)
            
            print(f"  Name: {plugin_info['name']}")
            print(f"  Version: {plugin_info['version']}")
            print(f"  Package: {plugin_info['package_size']} bytes")
            
            if register_plugin_to_db(args.db_host, args.db_port, plugin_info):
                print(f"  ✅ Registered successfully")
            else:
                print(f"  ❌ Registration failed")
        except Exception as e:
            print(f"  ❌ Error: {e}")

if __name__ == "__main__":
    main()
