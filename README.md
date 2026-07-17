# feishu_cli_assistant
飞书cli智能助手

# Feishu CLI Assistant 飞书cli智能助手

基于 LLM 的飞书自动化智能办公助手，通过 CLI 终端输入自然语言命令，AI 自动调用飞书 API 完成任务管理、会议安排、文档摘要、项目追踪、审批处理等办公场景。

## 功能命令

| 命令 | 权限 | 说明 |
|------|------|------|
| `health` | 所有人 | 系统健康检查 |
| `todo <content>` | 所有人 | 自然语言创建飞书任务 |
| `meet <desc>` | 所有人 | AI 自动安排会议（查忙闲、创建日历、邀请人员） |
| `brief <doc_url>` | 所有人 | AI 生成文档摘要并拆解子任务到多维表格 |
| `auth login/logout/whoami` | 所有人 | 飞书 OAuth 认证 |
| `track` | Admin | 项目进度追踪周报（含可视化卡片 + 饼图） |
| `cost` | Admin | 费用管理 |
| `audit` | Admin | 审批管理 |
| `execute <desc>` | Admin | AI 任务拆解 + 自动执行（5 步全自动流程） |
| `remind` | Admin | 催办提醒 |
| `form <project\|url>` | Admin | 多维表格数据分析 |
| `crm` | Admin | CRM 客户数据查询（支持简道云集成） |
| `admin users/set-role` | Admin | 用户角色管理 |

## 快速开始

### 环境要求

- Python 3.10+
- 飞书开放平台应用（需提前创建并获取凭证）

### 1. 安装依赖

```powershell
# 服务端
cd server
pip install -r requirements.txt

# 客户端
cd client
pip install -r requirements.txt
```

### 2. 配置服务端

复制 `server/.env.example` 为 `server/.env`，编辑以下配置：

```env
# 服务端口
SERVER_HOST=0.0.0.0
SERVER_PORT=8000

# 数据库（默认 SQLite，也可切换为 MySQL）
DB_TYPE=sqlite
# DB_TYPE=mysql
# DB_HOST=localhost
# DB_PORT=3306
# DB_USER=your_db_user
# DB_PASSWORD=your_db_password
# DB_NAME=lanshan_ai_agent

# 飞书应用凭证（从飞书开放平台获取）
FEISHU_APP_ID=your_app_id
FEISHU_APP_SECRET=your_app_secret
FEISHU_REDIRECT_URI=http://localhost:8000/api/v1/auth/callback

# LLM 配置（支持 openai / qwen / deepseek / zhipu）
LLM_PROVIDER=openai
LLM_API_KEY=your_api_key
LLM_MODEL=gpt-4o

# 客户端认证 Token（自行设置随机字符串，客户端需一致）
AUTH_TOKEN=your_auth_token

# Admin 权限判定（满足任一条件即为 Admin）
ADMIN_DEPARTMENT_IDS=
ADMIN_JOB_LEVEL_IDS=
ADMIN_EMPLOYEE_TYPES=正式员工
AUTH_FALLBACK_MODE=relaxed
```

### 3. 配置客户端

复制 `client/.env.example` 为 `client/.env`，编辑：

```env
SERVER_URL=http://localhost:8000
AUTH_TOKEN=your_auth_token      # 与服务端 AUTH_TOKEN 一致

USER_NAME=your_name
USER_ROLE=member
USER_OPEN_ID=your_feishu_open_id
```

### 4. 启动

```powershell
# 启动服务端
cd server
python main.py

# 启动客户端（新终端）
cd client
python main.py
```

也可直接双击项目根目录下的 `start_server.bat` 和 `start_client.bat`。

### 5. 使用

客户端启动后将进入交互式 CLI 界面，输入命令即可使用。首次使用需执行 `auth login` 完成飞书 OAuth 认证。

## 可选配置

### 简道云 CRM 集成

在 `server/.env` 中配置简道云 API 密钥和应用 ID，即可通过 `crm` 命令查询真实 CRM 数据。

### 多维表格映射

配置 `DEFAULT_BITABLE_APP_TOKEN` 和 `BITABLE_PROJECT_MAPPING`，实现项目名到多维表格的自动关联。
