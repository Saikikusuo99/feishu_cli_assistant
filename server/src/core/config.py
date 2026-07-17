"""服务端核心配置管理"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 优先加载 server/.env，不存在时加载项目根目录 .env
_server_env = Path(__file__).parent.parent.parent / ".env"  # server/src/core -> server/
if _server_env.exists():
    load_dotenv(_server_env)
load_dotenv()  # fallback


class ServerConfig:
    """服务端配置单例"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        # 服务配置
        self.host = os.getenv("SERVER_HOST", "0.0.0.0")
        self.port = int(os.getenv("SERVER_PORT", "8000"))
        self.debug = os.getenv("SERVER_DEBUG", "false").lower() == "true"

        # 数据库配置
        self.db_type = os.getenv("DB_TYPE", "mysql")  # mysql 或 sqlite
        self.db_host = os.getenv("DB_HOST", "localhost")
        self.db_port = int(os.getenv("DB_PORT", "3306"))
        self.db_user = os.getenv("DB_USER", "")
        self.db_password = os.getenv("DB_PASSWORD", "")
        self.db_name = os.getenv("DB_NAME", "lanshan_ai_agent")
        self.db_path = os.getenv("DB_PATH", str(Path(__file__).parent.parent.parent / "data" / "lanshan.db"))

        # 飞书配置
        self.feishu_app_id = os.getenv("FEISHU_APP_ID", "")
        self.feishu_app_secret = os.getenv("FEISHU_APP_SECRET", "")
        self.feishu_redirect_uri = os.getenv("FEISHU_REDIRECT_URI", "http://localhost:8000/api/v1/auth/callback")

        # LLM配置
        self.llm_api_key = os.getenv("LLM_API_KEY", "")
        self.llm_model = os.getenv("LLM_MODEL", "gpt-4o")
        self.llm_provider = os.getenv("LLM_PROVIDER", "openai")
        self.llm_temperature = float(os.getenv("LLM_TEMPERATURE", "0.7"))

        # 鉴权配置
        self.auth_token = os.getenv("AUTH_TOKEN", "")

        # 双重鉴权配置 - 飞书通讯录层级与自定义角色映射
        # Admin角色判定条件：满足任一条件即视为Admin
        self.admin_department_ids = os.getenv("ADMIN_DEPARTMENT_IDS", "")
        self.admin_job_level_ids = os.getenv("ADMIN_JOB_LEVEL_IDS", "")
        self.admin_employee_types = os.getenv("ADMIN_EMPLOYEE_TYPES", "")

        # 飞书API不可用时的降级策略："strict"（直接拒绝）或 "relaxed"（降级到本地角色校验）
        self.auth_fallback_mode = os.getenv("AUTH_FALLBACK_MODE", "relaxed")

        # 日志
        self.log_level = os.getenv("LOG_LEVEL", "INFO")

        # 群聊映射配置（格式：群聊名称=chat_id）
        self.chat_mapping = {}
        chat_map_env = os.getenv("FEISHU_CHAT_MAPPING", "")
        if chat_map_env:
            for item in chat_map_env.split(";"):
                if "=" in item:
                    name, chat_id = item.split("=", 1)
                    self.chat_mapping[name.strip()] = chat_id.strip()

        # 成员open_id映射配置（格式：成员名=open_id）
        self.member_mapping = {}
        member_map_env = os.getenv("FEISHU_MEMBER_MAPPING", "")
        if member_map_env:
            for item in member_map_env.split(";"):
                if "=" in item:
                    name, open_id = item.split("=", 1)
                    self.member_mapping[name.strip()] = open_id.strip()

        # ========== 简道云CRM配置 ==========
        # 访问 https://hc.jiandaoyun.com 开放平台获取
        self.jiandaoyun_api_key = os.getenv("JIANDAOYUN_API_KEY", "")
        self.jiandaoyun_app_id = os.getenv("JIANDAOYUN_APP_ID", "")
        self.jiandaoyun_customer_entry_id = os.getenv("JIANDAOYUN_CUSTOMER_ENTRY_ID", "")
        # 字段映射（格式：标准名=表单字段ID,标准名=表单字段ID）
        self.jiandaoyun_field_mapping = {}
        field_map_env = os.getenv("JIANDAOYUN_FIELD_MAPPING", "")
        if field_map_env:
            for item in field_map_env.split(","):
                if "=" in item:
                    k, v = item.split("=", 1)
                    self.jiandaoyun_field_mapping[k.strip()] = v.strip()

        # 默认多维表格配置
        self.default_bitable_app_token = os.getenv("DEFAULT_BITABLE_APP_TOKEN", "")
        self.default_bitable_table_id = os.getenv("DEFAULT_BITABLE_TABLE_ID", "")

        # 项目名到多维表格的映射配置（格式：项目名=app_token:table_id）
        self.bitable_project_mapping = {}
        project_map_env = os.getenv("BITABLE_PROJECT_MAPPING", "")
        if project_map_env:
            for item in project_map_env.split(";"):
                if "=" in item:
                    name, config = item.split("=", 1)
                    parts = config.split(":")
                    app_token = parts[0].strip()
                    table_id = parts[1].strip() if len(parts) > 1 else ""
                    self.bitable_project_mapping[name.strip()] = {
                        "app_token": app_token,
                        "table_id": table_id,
                    }

    @property
    def database_url(self) -> str:
        if self.db_type == "sqlite":
            return f"sqlite+aiosqlite:///{self.db_path}"
        return (
            f"mysql+aiomysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_url_sync(self) -> str:
        if self.db_type == "sqlite":
            return f"sqlite:///{self.db_path}"
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


server_config = ServerConfig()
