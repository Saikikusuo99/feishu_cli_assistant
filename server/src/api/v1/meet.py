"""会议 API 路由

POST /api/v1/meet/schedule - AI智能安排会议
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from server.src.services.meet_service import schedule_meeting
from server.src.core.auth import require_member

router = APIRouter(prefix="/meet", tags=["meet"])


class MeetScheduleRequest(BaseModel):
    user_input: str
    user_open_id: str = ""
    user_access_token: str = ""
    user_name: str = ""
    confirm: bool = False


@router.post("/schedule")
async def meet_schedule(
    req: MeetScheduleRequest,
    user=Depends(require_member),
):
    """AI智能安排会议

    使用AI解析自然语言输入，自动搜索参会人、查询忙闲、创建会议。
    """
    return await schedule_meeting(
        user_input=req.user_input,
        user_open_id=req.user_open_id,
        user_access_token=req.user_access_token,
        user_name=req.user_name,
        confirm=req.confirm,
    )
