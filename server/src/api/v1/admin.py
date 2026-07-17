"""管理员用户管理 API

提供用户列表查看和角色管理功能。
所有接口均受双重鉴权保护（require_admin），仅管理员可访问。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.src.core.auth import require_admin
from server.src.db.session import get_db_session
from server.src.db.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


class SetRoleRequest(BaseModel):
    open_id: str
    role: str  # "admin" 或 "member"


@router.get("/users")
async def list_users(
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """列出所有用户

    需要 Admin 权限。
    """
    result = await db.execute(select(User).order_by(User.id))
    users = result.scalars().all()
    return {
        "ok": True,
        "users": [
            {
                "id": u.id,
                "open_id": u.open_id,
                "name": u.name,
                "role": u.role,
                "department_ids": u.department_ids,
                "employee_type": u.employee_type,
            }
            for u in users
        ],
    }


@router.post("/users/set-role")
async def set_user_role(
    req: SetRoleRequest,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """设置用户角色

    需要 Admin 权限。
    只能设置为 admin 或 member。
    """
    if req.role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="角色只能是 admin 或 member")

    result = await db.execute(select(User).where(User.open_id == req.open_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail=f"用户不存在: {req.open_id}")

    old_role = target.role
    target.role = req.role
    await db.commit()

    return {
        "ok": True,
        "message": f"用户 {target.name} 角色已从 {old_role} 改为 {req.role}",
    }