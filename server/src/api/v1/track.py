"""项目进度追踪 API 路由

POST /api/v1/track        - 生成项目进度报告并发送
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from server.src.services.track_service import generate_progress_report
from server.src.core.auth import require_admin

router = APIRouter(prefix="/track", tags=["track"])


class TrackRequest(BaseModel):
    input_text: str
    user_access_token: str = ""


@router.post("")
async def track_progress(
    req: TrackRequest,
    user=Depends(require_admin),
):
    """生成项目进度周报并通过机器人发送

    支持两种模式：
    1. 自然语言：通过 Drive API 搜索多维表格，遍历所有数据表
    2. URL：解析飞书多维表格链接，提取 app_token 和 table_id

    Args:
        input_text: 项目名称（自然语言）或飞书多维表格 URL
        user_access_token: 用户访问令牌
    """
    target_chat_id = ""

    if req.user_access_token:
        from server.src.services.auth_service import oauth_service
        user_info = await oauth_service.get_user_info(req.user_access_token)
        if user_info.get("ok"):
            target_chat_id = user_info.get("open_id", "")

    return await generate_progress_report(
        input_text=req.input_text,
        chat_id=target_chat_id,
        user_access_token=req.user_access_token,
    )