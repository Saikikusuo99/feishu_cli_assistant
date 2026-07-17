"""OAuth认证 API 路由

GET  /api/v1/auth/url      - 获取授权URL
GET  /api/v1/auth/callback - 授权回调
POST /api/v1/auth/token    - 获取/刷新token
GET  /api/v1/auth/user     - 获取用户信息
GET  /api/v1/auth/whoami   - 获取当前用户身份（启动鉴权）
"""

from fastapi import APIRouter, Body, Depends, Query, HTTPException
from fastapi.responses import RedirectResponse

from server.src.services.auth_service import oauth_service
from server.src.core.auth import require_member

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/url")
def auth_url(state: str = ""):
    """获取飞书OAuth授权URL

    Args:
        state: 可选的状态参数

    Returns:
        授权URL
    """
    if not oauth_service.is_configured:
        return {"ok": False, "error": "OAuth未配置"}

    auth_url = oauth_service.get_auth_url(state)
    return {"ok": True, "auth_url": auth_url}


@router.get("/callback")
async def auth_callback(code: str = Query(...), state: str = ""):
    """OAuth授权回调

    用户在飞书授权后，飞书会重定向到此URL并携带code参数

    Args:
        code: 授权码
        state: 状态参数（可选）

    Returns:
        授权结果（包含完整token信息）
    """
    if not code:
        raise HTTPException(status_code=400, detail="缺少授权码")

    result = await oauth_service.get_user_token(code)
    if result.get("error"):
        return {"ok": False, "error": result.get("msg", "授权失败")}

    return {
        "ok": True,
        "message": "授权成功",
        "open_id": result.get("open_id", ""),
        "tenant_key": result.get("tenant_key", ""),
        "expire": result.get("expire", 7200),
        "access_token": result.get("access_token", ""),
        "refresh_token": result.get("refresh_token", ""),
        "code": code,
    }


@router.post("/token")
async def get_token(code: str = Body(""), refresh_token: str = Body("")):
    """获取或刷新用户token

    Args:
        code: 授权码（用于首次获取）
        refresh_token: 刷新令牌（用于刷新）

    Returns:
        token信息
    """
    if code:
        result = await oauth_service.get_user_token(code)
    elif refresh_token:
        result = await oauth_service.refresh_user_token(refresh_token)
    else:
        return {"ok": False, "msg": "需要提供code或refresh_token"}

    return result


@router.get("/user")
async def get_user_info(access_token: str = Query("")):
    """获取用户信息

    Args:
        access_token: user_access_token（可选，不传则返回服务器存储的token）

    Returns:
        用户信息
    """
    if access_token:
        result = await oauth_service.get_user_info(access_token)
    else:
        result = oauth_service.get_stored_user_info()
    return result


@router.get("/sync_token")
async def sync_token():
    """同步用户token（供客户端初始化时使用）
    
    使用internal auth token访问，返回服务器存储的最新用户token
    
    Returns:
        token信息
    """
    return oauth_service.get_stored_user_info()


@router.get("/whoami")
async def whoami(user=Depends(require_member)):
    """获取当前用户身份（客户端启动时调用，完成双重鉴权）

    客户端启动时调用此接口，服务端执行完整的双重鉴权：
    1. 飞书通讯录层级校验
    2. 本地数据库角色校验

    Returns:
        { ok, role, name, open_id }
    """
    if user is None:
        # 开发模式（AUTH_TOKEN 绕过）
        return {"ok": True, "role": "admin", "name": "开发模式"}

    return {
        "ok": True,
        "role": user.role,
        "name": user.name,
        "open_id": user.open_id,
    }
