"""FastAPI 应用入口

创建FastAPI应用实例，注册中间件和路由。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.src.core.config import server_config
from server.src.api.v1.health import router as health_router
from server.src.api.v1.auth import router as auth_router
from server.src.api.v1.todo import router as todo_router
from server.src.api.v1.meet import router as meet_router
from server.src.api.v1.brief import router as brief_router
from server.src.api.v1.track import router as track_router
from server.src.api.v1.cost import router as cost_router
from server.src.api.v1.audit import router as audit_router
from server.src.api.v1.execute import router as execute_router
from server.src.api.v1.remind import router as remind_router
from server.src.api.v1.form import router as form_router
from server.src.api.v1.crm import router as crm_router
from server.src.api.v1.admin import router as admin_router
from server.src.core.auth import auth_middleware


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Agent 飞书自动化系统",
        description="CLI客户端 + FastAPI后端服务，实现AI驱动的飞书办公自动化",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # 全局鉴权中间件
    app.middleware("http")(auth_middleware)

    # CORS中间件（允许CLI客户端跨域访问）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(todo_router, prefix="/api/v1")
    app.include_router(meet_router, prefix="/api/v1")
    app.include_router(brief_router, prefix="/api/v1")
    app.include_router(track_router, prefix="/api/v1")
    app.include_router(cost_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(execute_router, prefix="/api/v1")
    app.include_router(remind_router, prefix="/api/v1")
    app.include_router(form_router, prefix="/api/v1")
    app.include_router(crm_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")

    # 根路径也支持健康检查
    @app.get("/")
    async def root():
        return {
            "service": "AI Agent 飞书自动化系统",
            "version": "1.0.0",
            "docs": "/docs",
        }

    return app
