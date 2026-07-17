"""双重鉴权中间件与依赖

支持：
1. 飞书通讯录层级校验（department_ids, job_level_id, employee_type）
2. 本地角色校验（Admin/Member）
3. 双重鉴权：两次校验全部通过后才允许执行
4. 飞书API不可用时的降级策略
"""

import logging
from fastapi import Request, Header, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from server.src.core.config import server_config
from server.src.db.session import get_db_session
from server.src.db.models.user import User

security = HTTPBearer(auto_error=False)
logger = logging.getLogger("lanshan-server.auth")


def _parse_config_list(config_str: str) -> list:
    """解析配置列表字符串（逗号分隔）"""
    if not config_str:
        return []
    return [item.strip() for item in config_str.split(",") if item.strip()]


async def _check_feishu_hierarchy(
    user_open_id: str,
    user_access_token: str = "",
) -> dict:
    """基于飞书通讯录的基础权限校验

    获取用户的department_ids、employee_type、job_level_id等属性，
    判断是否满足Admin权限范围。

    Returns:
        {
            "ok": bool,
            "is_admin": bool,
            "department_ids": list,
            "job_level_id": str,
            "employee_type": str,
            "error": str (可选)
        }
    """
    try:
        from server.src.feishu.client import feishu_client

        if not feishu_client.is_configured:
            logger.info("飞书客户端未配置，跳过通讯录层级校验")
            return {"ok": True, "is_admin": False, "department_ids": [], "job_level_id": "", "employee_type": ""}

        result = await feishu_client.get_user_full_info(user_open_id, user_access_token=user_access_token)

        if result.get("code") != 0:
            logger.warning(f"获取飞书用户信息失败: {result.get('msg', '未知错误')}")
            return {"ok": False, "is_admin": False, "department_ids": [], "job_level_id": "", "employee_type": "", "error": result.get("msg", "获取用户信息失败")}

        user_data = result.get("data", {}).get("user", {})
        department_ids = user_data.get("department_ids", [])
        job_level_id = user_data.get("job_level_id", "")
        employee_type = user_data.get("employee_type", "")

        admin_dept_ids = _parse_config_list(server_config.admin_department_ids)
        admin_job_level_ids = _parse_config_list(server_config.admin_job_level_ids)
        admin_employee_types = _parse_config_list(server_config.admin_employee_types)

        is_admin = False
        if admin_dept_ids and department_ids:
            for dept_id in department_ids:
                if dept_id in admin_dept_ids:
                    is_admin = True
                    logger.info(f"用户 {user_open_id} 在Admin部门范围中: {dept_id}")
                    break

        if not is_admin and admin_job_level_ids and job_level_id:
            if job_level_id in admin_job_level_ids:
                is_admin = True
                logger.info(f"用户 {user_open_id} 在Admin职级范围中: {job_level_id}")

        if not is_admin and admin_employee_types and employee_type:
            if employee_type in admin_employee_types:
                is_admin = True
                logger.info(f"用户 {user_open_id} 在Admin员工类型范围中: {employee_type}")

        return {
            "ok": True,
            "is_admin": is_admin,
            "department_ids": department_ids,
            "job_level_id": job_level_id,
            "employee_type": employee_type,
        }

    except Exception as e:
        logger.error(f"飞书通讯录校验异常: {e}")
        return {"ok": False, "is_admin": False, "department_ids": [], "job_level_id": "", "employee_type": "", "error": str(e)}


async def auth_middleware(request: Request, call_next):
    """
    简易鉴权中间件。
    MVP阶段通过请求头中的 Authorization Bearer token 进行校验。
    集成飞书通讯录层级 + 本地角色的双重鉴权。
    """
    # OAuth认证流程相关路径跳过AUTH_TOKEN校验
    # 这些端点有自身的OAuth安全机制（code交换、refresh_token验证等）
    path = request.url.path
    if path.startswith("/api/v1/auth/") and path != "/api/v1/auth/whoami":
        return await call_next(request)

    skip_paths = [
        "/health",
        "/api/v1/health",
        "/docs",
        "/openapi.json",
    ]
    if path in skip_paths:
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少认证令牌。请在请求头中提供 Authorization: Bearer <token>",
        )

    token = auth_header[7:]
    if token != server_config.auth_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="认证令牌无效",
        )

    return await call_next(request)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db_session),
) -> User | None:
    """获取当前用户（Legacy：通过 Authorization Bearer token 查找）

    通过请求头中的access_token或open_id查询用户信息。
    返回用户对象或None（未登录时）。
    """
    if not credentials or not credentials.credentials:
        return None

    token = credentials.credentials
    if token == server_config.auth_token:
        return None

    result = await db.execute(select(User).where(User.open_id == token))
    user = result.scalar_one_or_none()
    return user


async def get_current_user_v2(
    x_user_open_id: str = Header(None, alias="X-User-Open-Id"),
    db: AsyncSession = Depends(get_db_session),
) -> User | None:
    """获取当前用户（通过 X-User-Open-Id 请求头）

    客户端通过 OAuth 登录后获取 open_id，每次请求时通过此请求头发送。
    服务端据此查找数据库中的用户记录，实现基于用户身份的鉴权。

    Returns:
        用户对象（DB中存在且有角色时），或None（未登录 / 开发模式）
    """
    if not x_user_open_id:
        return None

    result = await db.execute(select(User).where(User.open_id == x_user_open_id))
    return result.scalar_one_or_none()


async def _sync_user_contacts_info(
    db: AsyncSession,
    user: User,
    contacts_info: dict,
) -> None:
    """同步用户通讯录信息到数据库"""
    user.department_ids = ",".join(contacts_info.get("department_ids", []))
    user.job_level_id = contacts_info.get("job_level_id", "")
    user.employee_type = contacts_info.get("employee_type", "")
    await db.commit()
    logger.info(f"用户 {user.open_id} 通讯录信息已更新")


async def require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user: User | None = Depends(get_current_user_v2),
    db: AsyncSession = Depends(get_db_session),
) -> User | None:
    """要求Admin角色（双重鉴权）

    步骤一：飞书通讯录层级校验
    - 获取用户department_ids、employee_type、job_level_id
    - 判断是否在Admin权限范围内

    步骤二：本地角色二次校验
    - 检查数据库中用户角色是否为admin

    步骤三：最终判定
    - 两次校验全部通过后才允许执行
    - 开发模式下，如果token是开发token，直接授予Admin权限

    降级策略：
    - strict模式：飞书API不可用时直接拒绝
    - relaxed模式：飞书API不可用时降级到仅本地角色校验
    - 注意：飞书API成功但用户不满足Admin条件时，始终拒绝，不受降级策略影响
    """
    if credentials and credentials.credentials == server_config.auth_token:
        logger.info("开发模式：使用开发token，直接授予Admin权限")
        return None

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户未登录",
        )

    # 获取用户access_token用于飞书通讯录查询
    user_access_token = ""
    if user and user.open_id:
        try:
            from server.src.services.auth_service import oauth_service
            user_access_token = await oauth_service.get_user_token_by_open_id_async(user.open_id) or ""
        except Exception as e:
            logger.warning(f"获取用户token失败: {e}")

    contacts_result = await _check_feishu_hierarchy(user.open_id, user_access_token=user_access_token)

    if not contacts_result.get("ok"):
        if server_config.auth_fallback_mode == "strict":
            logger.warning(f"飞书通讯录校验失败，严格模式下拒绝请求: {contacts_result.get('error')}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="飞书通讯录校验失败，请稍后重试",
            )
        else:
            logger.warning(f"飞书通讯录校验失败，降级到本地角色校验: {contacts_result.get('error')}")
            if user.role != "admin":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="需要Admin权限（飞书通讯录校验失败，降级检查本地角色）",
                )
            logger.info(f"用户 {user.open_id} 通过本地角色校验")
            return user

    is_feishu_admin = contacts_result.get("is_admin", False)

    await _sync_user_contacts_info(db, user, contacts_result)

    if not is_feishu_admin:
        logger.info(f"用户 {user.open_id} 不在飞书Admin权限范围内")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要Admin权限（部门层级不足）",
        )

    if user.role != "admin":
        logger.info(f"用户 {user.open_id} 本地角色不是admin")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要Admin权限（本地角色不足）",
        )

    logger.info(f"用户 {user.open_id} 双重鉴权通过：飞书层级=Admin，本地角色=admin")
    return user


async def require_member(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user: User | None = Depends(get_current_user_v2),
    db: AsyncSession = Depends(get_db_session),
) -> User | None:
    """要求Member角色（双重鉴权）

    步骤一：飞书通讯录层级校验
    - 验证用户是否为企业合法用户

    步骤二：本地角色二次校验
    - 检查数据库中用户角色是否为合法角色（admin或member）

    步骤三：最终判定
    - 两次校验全部通过后才允许执行
    - 开发模式下，如果token是开发token，直接授予权限
    """
    if credentials and credentials.credentials == server_config.auth_token:
        logger.info("开发模式：使用开发token，直接授予Member权限")
        return None

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户未登录",
        )

    # 获取用户access_token用于飞书通讯录查询
    user_access_token = ""
    if user and user.open_id:
        try:
            from server.src.services.auth_service import oauth_service
            user_access_token = await oauth_service.get_user_token_by_open_id_async(user.open_id) or ""
        except Exception as e:
            logger.warning(f"获取用户token失败: {e}")

    contacts_result = await _check_feishu_hierarchy(user.open_id, user_access_token=user_access_token)

    if not contacts_result.get("ok"):
        if server_config.auth_fallback_mode == "strict":
            logger.warning(f"飞书通讯录校验失败，严格模式下拒绝请求: {contacts_result.get('error')}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="飞书通讯录校验失败，请稍后重试",
            )
        else:
            logger.warning(f"飞书通讯录校验失败，降级到本地角色校验")

    await _sync_user_contacts_info(db, user, contacts_result)

    if user.role not in ("admin", "member"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户角色无效",
        )

    logger.info(f"用户 {user.open_id} Member鉴权通过：角色={user.role}")
    return user