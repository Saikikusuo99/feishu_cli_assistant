"""待办任务 API 路由

POST /api/v1/todo - 创建任务（AI解析自然语言输入）

需要 Member 权限（双重鉴权保护）。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from server.src.core.auth import require_member
from server.src.services.todo_service import create_todo

router = APIRouter(prefix="/todo", tags=["todo"])


class TodoCreateRequest(BaseModel):
    content: str
    user_open_id: str = ""
    user_access_token: str = ""
    sync_chat: str = ""


@router.post("")
async def todo_create(
    req: TodoCreateRequest,
    _user=Depends(require_member),
):
    """创建飞书任务

    AI自动解析自然语言输入，提取任务内容、截止日期和群聊名称。
    如果输入中包含"同步"、"发送到"等关键词，会自动同步任务到指定群聊。

    需要 Member 权限。
    """
    result = await create_todo(
        user_input=req.content,
        user_open_id=req.user_open_id,
        user_access_token=req.user_access_token,
        sync_chat=req.sync_chat,
    )
    return result