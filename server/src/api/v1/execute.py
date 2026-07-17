"""AI任务拆解引擎 API 路由

POST /api/v1/execute        - 拆解目标并生成执行计划
POST /api/v1/execute/run    - 执行计划
GET  /api/v1/execute/status - 获取执行状态
POST /api/v1/execute/control - 控制执行计划（暂停/继续/终止）
"""

from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel

from server.src.services.execute_service import decompose_task, execute_plan, get_execution_status, control_execution
from server.src.core.auth import require_admin

router = APIRouter(prefix="/execute", tags=["execute"])


class ExecuteRequest(BaseModel):
    goal: str
    user_access_token: str = ""
    user_open_id: str = ""


class ExecuteRunRequest(BaseModel):
    execution_id: str
    user_access_token: str = ""
    user_open_id: str = ""
    user_name: str = ""


class ExecuteControlRequest(BaseModel):
    execution_id: str
    action: str


@router.post("")
async def execute_decompose(
    req: ExecuteRequest,
    user=Depends(require_admin),
):
    """拆解目标并生成执行计划

    Args:
        goal: 用户输入的目标
    """
    return await decompose_task(req.goal)


@router.post("/run")
async def execute_run(
    req: ExecuteRunRequest,
    user=Depends(require_admin),
):
    """执行计划

    Args:
        execution_id: 执行计划ID
    """
    return await execute_plan(
        execution_id=req.execution_id,
        user_access_token=req.user_access_token,
        user_open_id=req.user_open_id,
        user_name=req.user_name,
    )


@router.get("/status")
async def execute_status(
    execution_id: str = Query(..., description="执行计划ID"),
    user=Depends(require_admin),
):
    """获取执行计划状态"""
    return await get_execution_status(execution_id)


@router.post("/control")
async def execute_control(
    req: ExecuteControlRequest,
    user=Depends(require_admin),
):
    """控制执行计划

    Args:
        execution_id: 执行计划ID
        action: pause/resume/cancel
    """
    return await control_execution(
        execution_id=req.execution_id,
        action=req.action,
    )
