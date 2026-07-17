"""健康检查与测试端点

提供系统健康检查、飞书链路测试、LLM链路测试等接口。
"""

import logging
from fastapi import APIRouter
from sqlalchemy import text

from server.src.db.session import get_db_session
from server.src.feishu.client import feishu_client
from server.src.ai.llm import llm_engine

logger = logging.getLogger("lanshan-server.api")
router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """基础健康检查"""
    return {
        "status": "ok",
        "service": "lanshan-ai-agent",
        "version": "0.1.0",
    }


@router.get("/health/db")
async def health_db():
    """数据库连接检查"""
    try:
        async for session in get_db_session():
            await session.execute(text("SELECT 1"))
            return {"status": "ok", "message": "MySQL数据库连接正常"}
    except Exception as e:
        return {"status": "error", "message": f"数据库连接失败: {str(e)}"}


@router.get("/health/feishu")
async def health_feishu():
    """飞书API连接检查"""
    if not feishu_client.is_configured:
        return {
            "status": "error",
            "message": "飞书未配置。请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET",
        }
    result = await feishu_client.health_check()
    return {"status": "ok" if result["ok"] else "error", **result}


@router.get("/health/llm")
async def health_llm():
    """LLM连接检查"""
    if not llm_engine.is_configured:
        return {
            "status": "error",
            "message": "LLM未配置。请设置 LLM_API_KEY 和 LLM_MODEL",
        }
    result = await llm_engine.health_check()
    return {"status": "ok" if result["ok"] else "error", **result}


@router.get("/health/full")
async def health_full():
    """全链路健康检查"""
    results = {
        "service": {"status": "ok", "message": "服务运行正常"},
        "database": {},
        "feishu": {},
        "llm": {},
    }

    # 数据库检查
    try:
        async for session in get_db_session():
            await session.execute(text("SELECT 1"))
            results["database"] = {"status": "ok", "message": "MySQL连接正常"}
    except Exception as e:
        results["database"] = {"status": "error", "message": str(e)}

    # 飞书检查
    if feishu_client.is_configured:
        fs_result = await feishu_client.health_check()
        results["feishu"] = {"status": "ok" if fs_result["ok"] else "error", **fs_result}
    else:
        results["feishu"] = {"status": "skipped", "message": "飞书未配置"}

    # LLM检查
    if llm_engine.is_configured:
        llm_result = await llm_engine.health_check()
        results["llm"] = {"status": "ok" if llm_result["ok"] else "error", **llm_result}
    else:
        results["llm"] = {"status": "skipped", "message": "LLM未配置"}

    overall_ok = all(
        r.get("status") in ("ok", "skipped") for r in results.values()
        if isinstance(r, dict)
    )
    results["overall"] = "ok" if overall_ok else "error"
    return results
