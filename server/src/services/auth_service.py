"""OAuth认证服务

处理飞书OAuth 2.0授权流程：
1. 获取授权URL
2. 通过code换取user_access_token
3. 刷新user_access_token（自动续期）
4. 验证token有效性
5. Token持久化存储（JSON文件）
"""

import time
import httpx
import logging
import json
import os
import webbrowser
from typing import Optional, Dict, Any
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from server.src.core.config import server_config

logger = logging.getLogger("lanshan-server.auth")

_TOKEN_STORAGE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".user_tokens.json")

_user_tokens: Dict[str, Dict[str, Any]] = {}


def _load_tokens_from_file():
    global _user_tokens
    try:
        if os.path.exists(_TOKEN_STORAGE_FILE):
            with open(_TOKEN_STORAGE_FILE, "r", encoding="utf-8") as f:
                _user_tokens = json.load(f)
            logger.info(f"从文件加载了 {len(_user_tokens)} 个用户token")
        else:
            _user_tokens = {}
    except Exception as e:
        logger.warning(f"加载token文件失败: {e}")
        _user_tokens = {}


def _save_tokens_to_file():
    try:
        with open(_TOKEN_STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(_user_tokens, f, indent=2, ensure_ascii=False)
        logger.debug("Token已保存到文件")
    except Exception as e:
        logger.warning(f"保存token文件失败: {e}")


_load_tokens_from_file()


class OAuthService:
    """飞书OAuth服务"""

    AUTH_URL = "https://open.feishu.cn/open-apis/authen/v1/index"
    TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/access_token"
    REFRESH_URL = "https://open.feishu.cn/open-apis/authen/v1/refresh_access_token"
    USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
    
    DEFAULT_SCOPES = [
        "calendar:readonly",
        "calendar:write",
        "task:task",
        "task:task:writeonly",
        "contact:readonly",
        "docs:readonly",
        "drive:readonly",
        "im:message",
        "im:message:write",
        "bitable:app:readonly",
        "bitable:app",
        "base:table:read",
        "base:record:retrieve",
        "base:record:read",
        "offline_access",
    ]

    def __init__(self):
        self.app_id = server_config.feishu_app_id
        self.app_secret = server_config.feishu_app_secret
        self.redirect_uri = server_config.feishu_redirect_uri

    @property
    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_secret and self.redirect_uri)

    def get_auth_url(self, state: str = "", scopes: list = None) -> str:
        import urllib.parse

        scope_list = scopes or self.DEFAULT_SCOPES
        params = {
            "app_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "state": state or "lanshan-oauth",
            "scope": " ".join(scope_list),
        }
        return f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"

    async def _sync_user_to_db(self, open_id: str, name: str = "") -> None:
        """同步用户信息到数据库"""
        try:
            from sqlalchemy import select
            from server.src.db.models.user import User
            from server.src.db.session import async_session_factory

            async with async_session_factory() as session:
                result = await session.execute(select(User).where(User.open_id == open_id))
                user = result.scalar_one_or_none()

                if user:
                    if name:
                        user.name = name
                    await session.commit()
                    logger.info(f"用户 {open_id} 信息已更新")
                else:
                    user = User(
                        open_id=open_id,
                        name=name or "未知用户",
                        role="member",
                    )
                    session.add(user)
                    await session.commit()
                    logger.info(f"用户 {open_id} 已创建")

        except Exception as e:
            logger.error(f"同步用户信息到数据库失败: {e}")

    async def get_user_token(self, code: str) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    self.TOKEN_URL,
                    json={
                        "app_id": self.app_id,
                        "app_secret": self.app_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("code") != 0:
                logger.error(f"获取user_access_token失败: {data}")
                return {"error": True, "msg": data.get("msg", "获取token失败")}

            token_data = data.get("data", {})
            user_id = token_data.get("open_id", "")
            access_token = token_data.get("access_token", "")

            if user_id:
                _user_tokens[user_id] = {
                    "access_token": access_token,
                    "refresh_token": token_data.get("refresh_token", ""),
                    "expire": token_data.get("expire", 7200),
                    "expires_at": time.time() + token_data.get("expire", 7200),
                    "open_id": user_id,
                    "tenant_key": token_data.get("tenant_key", ""),
                }
                _save_tokens_to_file()
                logger.info(f"用户 {user_id} 授权成功，token已持久化")

                user_info_result = await self.get_user_info(access_token)
                user_name = ""
                if user_info_result.get("ok"):
                    user_name = user_info_result.get("name", "")

                await self._sync_user_to_db(user_id, user_name)

            return {"ok": True, **token_data}

        except Exception as e:
            logger.error(f"获取user_access_token异常: {e}")
            return {"error": True, "msg": str(e)}

    async def refresh_user_token(self, refresh_token: str) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                app_token_resp = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
                    json={"app_id": self.app_id, "app_secret": self.app_secret},
                )
                app_token_data = app_token_resp.json()
                app_access_token = app_token_data.get("app_access_token", "")
                if not app_access_token:
                    logger.error(f"获取app_access_token失败: {app_token_data}")
                    return {"error": True, "msg": "获取app_access_token失败"}

                resp = await client.post(
                    self.REFRESH_URL,
                    json={
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    headers={"Authorization": f"Bearer {app_access_token}"},
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("code") != 0:
                logger.error(f"刷新user_access_token失败: {data}")
                return {"error": True, "msg": data.get("msg", "刷新token失败")}

            token_data = data.get("data", {})
            user_id = token_data.get("open_id", "")

            if user_id:
                _user_tokens[user_id] = {
                    "access_token": token_data.get("access_token", ""),
                    "refresh_token": token_data.get("refresh_token", ""),
                    "expire": token_data.get("expire", 7200),
                    "expires_at": time.time() + token_data.get("expire", 7200),
                    "open_id": user_id,
                    "tenant_key": token_data.get("tenant_key", ""),
                }
                _save_tokens_to_file()
                logger.info(f"用户 {user_id} token已刷新，token已持久化")

            return {"ok": True, **token_data}

        except Exception as e:
            logger.error(f"刷新user_access_token异常: {e}")
            return {"error": True, "msg": str(e)}

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    self.USER_INFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("code") != 0:
                return {"error": True, "msg": data.get("msg", "获取用户信息失败")}

            return {"ok": True, **data.get("data", {})}

        except Exception as e:
            logger.error(f"获取用户信息异常: {e}")
            return {"error": True, "msg": str(e)}

    async def get_user_token_by_open_id_async(self, open_id: str) -> Optional[str]:
        token_info = _user_tokens.get(open_id)
        if not token_info:
            return None

        if time.time() >= token_info.get("expires_at", 0) - 60:
            logger.info(f"用户 {open_id} token即将过期，尝试自动刷新")
            refresh_token = token_info.get("refresh_token")
            if refresh_token:
                try:
                    refresh_result = await self.refresh_user_token(refresh_token)
                    if refresh_result.get("ok"):
                        new_token_info = _user_tokens.get(open_id)
                        if new_token_info:
                            return new_token_info.get("access_token")
                    else:
                        logger.error(f"用户 {open_id} token刷新失败: {refresh_result.get('msg', '未知错误')}")
                except Exception as e:
                    logger.error(f"用户 {open_id} token刷新异常: {e}")
            else:
                logger.error(f"用户 {open_id} 没有refresh_token，无法自动刷新")
            return None

        return token_info.get("access_token")

    def store_user_token(self, open_id: str, access_token: str, refresh_token: str, expire: int, tenant_key: str = ""):
        _user_tokens[open_id] = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expire": expire,
            "expires_at": time.time() + expire,
            "open_id": open_id,
            "tenant_key": tenant_key,
        }
        _save_tokens_to_file()

    def remove_user_token(self, open_id: str):
        _user_tokens.pop(open_id, None)
        _save_tokens_to_file()

    def get_stored_user_info(self) -> Dict[str, Any]:
        if not _user_tokens:
            return {"ok": False, "msg": "服务器没有存储用户token"}
        
        open_id = list(_user_tokens.keys())[0]
        token_info = _user_tokens[open_id]
        
        return {
            "ok": True,
            "open_id": open_id,
            "access_token": token_info.get("access_token", ""),
            "refresh_token": token_info.get("refresh_token", ""),
            "expire": token_info.get("expire", 7200),
            "expires_at": token_info.get("expires_at", 0),
        }

    def get_user_token_info_by_open_id(self, open_id: str) -> Dict[str, Any]:
        token_info = _user_tokens.get(open_id)
        if not token_info:
            return {"ok": False, "msg": f"没有找到用户 {open_id} 的token信息"}
        
        return {
            "ok": True,
            "open_id": open_id,
            "access_token": token_info.get("access_token", ""),
            "refresh_token": token_info.get("refresh_token", ""),
            "expire": token_info.get("expire", 7200),
            "expires_at": token_info.get("expires_at", 0),
        }

    def get_user_token_info_by_access_token(self, access_token: str) -> Dict[str, Any]:
        for open_id, token_info in _user_tokens.items():
            if token_info.get("access_token") == access_token:
                return {
                    "ok": True,
                    "open_id": open_id,
                    "access_token": token_info.get("access_token", ""),
                    "refresh_token": token_info.get("refresh_token", ""),
                    "expire": token_info.get("expire", 7200),
                    "expires_at": token_info.get("expires_at", 0),
                }
        return {"ok": False, "msg": "没有找到匹配的用户token信息"}

    def auto_authorize(self, scopes: list = None) -> Dict[str, Any]:
        """自动化授权流程：启动本地HTTP服务器 + 自动打开浏览器 + 接收code + 换取token
        
        Returns:
            token信息或错误信息
        """
        auth_url = self.get_auth_url(scopes=scopes)
        
        parsed = urlparse(self.redirect_uri)
        callback_host = parsed.hostname or "localhost"
        callback_port = parsed.port or 8000
        callback_path = parsed.path
        
        code_received = {"code": None, "error": None}
        
        class AuthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                query = parse_qs(urlparse(self.path).query)
                code = query.get("code", [None])[0]
                error = query.get("error", [None])[0]
                
                if code:
                    code_received["code"] = code
                    self.send_response(200)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write("""
                        <html><body>
                        <h1>Auth Success!</h1>
                        <p>You can close this page and return to the terminal.</p>
                        </body></html>
                    """.encode("utf-8"))
                elif error:
                    code_received["error"] = error
                    self.send_response(400)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(f"<html><body><h1>Auth Failed: {error}</h1></body></html>".encode("utf-8"))
                else:
                    self.send_response(200)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write("""
                        <html><body>
                        <h1>Waiting for authorization...</h1>
                        <p>Please complete authorization on the Feishu page.</p>
                        </body></html>
                    """.encode("utf-8"))
            
            def log_message(self, format, *args):
                pass
        
        print(f"\n🚀 启动本地授权服务器，监听 http://{callback_host}:{callback_port}{callback_path}")
        print(f"🔗 正在打开浏览器访问授权页面...")
        
        try:
            server = HTTPServer((callback_host, callback_port), AuthHandler)
            webbrowser.open(auth_url)
            
            print(f"⏳ 等待用户授权（请在浏览器中完成授权）...")
            server.handle_request()
            
            if code_received["code"]:
                print(f"✅ 收到授权code")
                import asyncio
                result = asyncio.run(self.get_user_token(code_received["code"]))
                return result
            elif code_received["error"]:
                return {"error": True, "msg": f"授权失败: {code_received['error']}"}
            else:
                return {"error": True, "msg": "未收到授权code"}
        except Exception as e:
            return {"error": True, "msg": f"授权过程异常: {e}"}


oauth_service = OAuthService()