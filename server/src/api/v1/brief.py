"""文档摘要 API 路由

POST /api/v1/brief - 生成文档摘要，可选创建任务多维表格
"""

import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from server.src.services.brief_service import generate_brief
from server.src.core.auth import require_member

logger = logging.getLogger("lanshan-server.api")
router = APIRouter(prefix="/brief", tags=["brief"])


class BriefRequest(BaseModel):
    doc_url: str
    create_table: bool = False
    user_open_id: str = ""
    user_access_token: str = ""


@router.post("")
async def brief_generate(
    req: BriefRequest,
    user=Depends(require_member),
):
    """生成文档摘要

    读取飞书文档内容，使用AI生成摘要并拆解为子任务。
    可选：自动创建任务多维表格。
    """
    result = await generate_brief(
        doc_url=req.doc_url,
        create_table=req.create_table,
        user_open_id=req.user_open_id,
        user_access_token=req.user_access_token,
    )
    return result
