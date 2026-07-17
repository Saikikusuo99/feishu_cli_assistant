# Feishu CLI Assistant 飞书自动化系统

基于 LLM 的飞书办公自动化系统，通过 CLI 终端输入自然语言命令，AI 自动调用飞书 API 完成任务管理、会议安排、文档摘要、项目追踪、审批处理等办公场景。

## 可用功能

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
- 飞书开放平台应用（需提前创建并获取凭证）（详情见FEISHU_PERMISSIONS.md）

### 1. 安装依赖

```powershell
# 服务端
cd server
pip install -r requirements.txt

# 客户端
cd client
pip install -r requirements.txt
```

### 2. 启动

```powershell
# 启动服务端
cd server
python main.py

# 启动客户端（新终端）
cd client
python main.py
```

也可直接双击项目根目录下的 `start_server.bat` 和 `start_client.bat`。

### 3. 使用

客户端启动后将进入交互式 CLI 界面，输入命令即可使用。首次使用需执行 `auth login` 完成飞书 OAuth 认证。

## 可选配置

### 简道云 CRM 集成

在 `server/.env` 中配置简道云 API 密钥和应用 ID，即可通过 `crm` 命令查询真实 CRM 数据。

### 多维表格映射

配置 `DEFAULT_BITABLE_APP_TOKEN` 和 `BITABLE_PROJECT_MAPPING`，实现项目名到多维表格的自动关联。


