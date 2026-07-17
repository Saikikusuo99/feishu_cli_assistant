"""客户端配置管理"""

import os
import time
import json
from dotenv import load_dotenv

load_dotenv()

# 用户token缓存（内存存储）
_user_token_cache = {}

# Token持久化文件路径
_TOKEN_STORAGE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".user_tokens.json")


def _load_tokens_from_file() -> dict:
    """从文件加载token"""
    try:
        if os.path.exists(_TOKEN_STORAGE_FILE):
            with open(_TOKEN_STORAGE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        pass
    return {}


def _save_tokens_to_file(tokens: dict):
    """保存token到文件"""
    try:
        with open(_TOKEN_STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2, ensure_ascii=False)
    except Exception as e:
        pass


class ClientConfig:
    """客户端配置单例"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        self.server_url = os.getenv("SERVER_URL", "http://localhost:8000")
        self.auth_token = os.getenv("AUTH_TOKEN", "lanshan-dev-token")
        self.user_name = os.getenv("USER_NAME", "")
        self.user_role = os.getenv("USER_ROLE", "member")
        self.user_open_id = os.getenv("USER_OPEN_ID", "")
        _user_token_cache.update(_load_tokens_from_file())

    def is_admin(self) -> bool:
        return self.user_role.lower() == "admin"

    def set_user_token(self, open_id: str, access_token: str, refresh_token: str, expire: int):
        """存储用户token"""
        _user_token_cache[open_id] = {
            "open_id": open_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expire": expire,
            "expires_at": time.time() + expire,
        }
        _save_tokens_to_file(_user_token_cache)

    def get_user_token(self) -> dict | None:
        """获取当前用户token"""
        if not _user_token_cache:
            return None
        
        # 返回第一个（也是唯一一个）用户的token
        for token_info in _user_token_cache.values():
            return token_info
        return None

    def get_access_token(self) -> str:
        """获取access_token"""
        token_info = self.get_user_token()
        if not token_info:
            return ""
        
        if time.time() >= token_info.get("expires_at", 0):
            return ""
        
        return token_info.get("access_token", "")

    def get_open_id(self) -> str:
        """获取用户open_id"""
        token_info = self.get_user_token()
        return token_info.get("open_id", "") if token_info else ""

    def clear_user_token(self):
        """清除用户token"""
        _user_token_cache.clear()
        if os.path.exists(_TOKEN_STORAGE_FILE):
            os.remove(_TOKEN_STORAGE_FILE)


client_config = ClientConfig()
