"""HTTP客户端

封装与后端FastAPI服务的通信，包含token自动刷新机制。
"""

import httpx
import time
from utils.config import client_config
from utils.logger import get_logger

logger = get_logger()


class HTTPClient:
    """后端API HTTP客户端，支持自动刷新用户token"""

    def __init__(self):
        self.base_url = client_config.server_url.rstrip("/")
        self.token = client_config.auth_token
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        self._refresh_lock = False

    async def _refresh_user_token(self) -> bool:
        """自动刷新用户token
        
        Returns:
            是否刷新成功
        """
        token_info = client_config.get_user_token()
        if not token_info:
            return False

        refresh_token = token_info.get("refresh_token", "")
        if not refresh_token:
            logger.warning("没有refresh_token，无法自动刷新")
            return False

        logger.info("尝试自动刷新用户token...")
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v1/auth/token",
                    json={"refresh_token": refresh_token},
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                resp.raise_for_status()
                result = resp.json()

            if result.get("ok"):
                open_id = result.get("open_id", token_info.get("open_id", ""))
                access_token = result.get("access_token", "")
                refresh_token = result.get("refresh_token", "")
                expire = result.get("expire", 7200)

                client_config.set_user_token(open_id, access_token, refresh_token, expire)
                logger.info(f"用户token刷新成功，有效期 {expire//3600} 小时")
                return True
            else:
                logger.warning(f"token刷新失败: {result.get('msg', '未知错误')}")
                return False

        except Exception as e:
            logger.error(f"token刷新异常: {e}")
            return False

    async def _ensure_valid_token(self) -> str:
        """确保用户token有效，过期时自动刷新
        
        Returns:
            有效的access_token，如果无法获取则返回空字符串
        """
        access_token = client_config.get_access_token()
        
        if access_token:
            return access_token

        logger.debug("本地token无效或已过期，尝试自动刷新...")
        if await self._refresh_user_token():
            return client_config.get_access_token()

        logger.debug("自动刷新失败，尝试从服务端同步token...")
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/auth/sync_token",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                resp.raise_for_status()
                result = resp.json()

            if result.get("ok"):
                open_id = result.get("open_id", "")
                access_token = result.get("access_token", "")
                refresh_token = result.get("refresh_token", "")
                expire = result.get("expire", 7200)

                if access_token:
                    client_config.set_user_token(open_id, access_token, refresh_token, expire)
                    logger.info("从服务端同步token成功")
                    return access_token

        except Exception as e:
            logger.warning(f"从服务端同步token失败: {e}")

        return ""

    async def sync_token_from_server(self) -> bool:
        """从服务端同步token（客户端初始化时调用）
        
        Returns:
            是否同步成功
        """
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/auth/sync_token",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                resp.raise_for_status()
                result = resp.json()

            if result.get("ok"):
                open_id = result.get("open_id", "")
                access_token = result.get("access_token", "")
                refresh_token = result.get("refresh_token", "")
                expire = result.get("expire", 7200)

                if access_token:
                    client_config.set_user_token(open_id, access_token, refresh_token, expire)
                    logger.info("从服务端同步token成功")
                    return True

        except Exception as e:
            logger.warning(f"从服务端同步token失败: {e}")

        return False

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers.update(self.headers)

        # 自动注入用户 open_id，支持服务端基于用户身份的鉴权
        open_id = client_config.get_open_id()
        if open_id:
            headers["X-User-Open-Id"] = open_id

        params = kwargs.get("params") or {}
        json_data = kwargs.get("json") or {}
        user_access_token = params.get("user_access_token", "")
        if not user_access_token and not json_data.get("user_access_token"):
            valid_token = await self._ensure_valid_token()
            if valid_token:
                if "params" in kwargs and kwargs["params"] is not None:
                    kwargs["params"]["user_access_token"] = valid_token
                elif "json" in kwargs and kwargs["json"] is not None:
                    kwargs["json"]["user_access_token"] = valid_token
                else:
                    kwargs["params"] = {"user_access_token": valid_token}

        # 自动注入 user_open_id
        user_open_id = params.get("user_open_id", "")
        if not user_open_id and not json_data.get("user_open_id"):
            open_id = client_config.get_open_id()
            if open_id:
                if "params" in kwargs and kwargs["params"] is not None:
                    kwargs["params"]["user_open_id"] = open_id
                elif "json" in kwargs and kwargs["json"] is not None:
                    kwargs["json"]["user_open_id"] = open_id

        async with httpx.AsyncClient(timeout=120) as client:
            try:
                resp = await client.request(method, url, headers=headers, **kwargs)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    logger.info("收到401错误，尝试刷新token并重试...")
                    if await self._refresh_user_token():
                        valid_token = client_config.get_access_token()
                        if valid_token:
                            if "params" in kwargs and kwargs["params"] is not None:
                                kwargs["params"]["user_access_token"] = valid_token
                            elif "json" in kwargs and kwargs["json"] is not None:
                                kwargs["json"]["user_access_token"] = valid_token
                            elif "params" in kwargs:
                                kwargs["params"] = {"user_access_token": valid_token}
                            
                            resp = await client.request(method, url, headers=headers, **kwargs)
                            resp.raise_for_status()
                            logger.info("刷新token后重试成功")
                            return resp.json()
                
                    return {"ok": False, "error": "认证失败，请使用 'auth login' 重新授权"}
                
                if e.response.status_code == 403:
                    return {"ok": False, "error": "权限不足"}
                
                return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
            except httpx.ConnectError:
                return {"ok": False, "error": f"无法连接后端服务 ({self.base_url})，请确认服务已启动"}
            except httpx.TimeoutException:
                return {"ok": False, "error": "请求超时"}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def get(self, path: str, params: dict = None) -> dict:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, data: dict = None) -> dict:
        return await self._request("POST", path, json=data or {})

    async def health(self) -> dict:
        """健康检查"""
        return await self.get("/api/v1/health")

    async def health_full(self) -> dict:
        """全链路健康检查"""
        return await self.get("/api/v1/health/full")

    async def health_db(self) -> dict:
        """数据库健康检查"""
        return await self.get("/api/v1/health/db")

    async def health_feishu(self) -> dict:
        """飞书健康检查"""
        return await self.get("/api/v1/health/feishu")

    async def health_llm(self) -> dict:
        """LLM健康检查"""
        return await self.get("/api/v1/health/llm")


http_client = HTTPClient()
