# Feishu CLI Assistant - 飞书配置细则

---

## 1. 创建自建应用并获取凭证

1. 打开 [飞书开放平台](https://open.feishu.cn/app) → **+ 创建应用** → 企业自建应用
2. 填名称和描述 → 确定创建
3. 进入应用 → **凭证与基础信息**，复制以下字段填到 `server/.env`：

| 字段 | 示例 | env 变量 |
|------|------|----------|
| App ID（以 `cli_` 开头） | `cli_a1b2c3d4e5f6g7h8` | `FEISHU_APP_ID` |
| App Secret | `abcdef1234567890` | `FEISHU_APP_SECRET` |

> 注意：Secret 重置后旧的立即失效，请妥善保存。

---

## 2. 配置安全设置

进入应用 → **安全设置**，配置 3 项：

### 2.1 重定向 URL（OAuth 必需）

| 环境 | URL |
|------|-----|
| 本地开发 | `http://localhost:8000/api/v1/auth/callback` |
| 生产 | `https://你的域名/api/v1/auth/callback` |

⚠️ 必须与 `server/.env` 的 `FEISHU_REDIRECT_URI` **完全一致**（一个字符都不能差）

---

## 3. 应用权限配置

进入应用 → **权限管理**，按中文名搜索并开通，全部开通后到「版本管理与发布」发布一次才生效：

| 分类 | 后台权限中文名（搜索关键词） |
|------|------------------------------|
| 消息与群组 | 获取与发送群组、单聊中的消息 |
| 消息与群组 | 获取群组信息 |
| 消息与群组 | 创建群组 |
| 消息与群组 | 发布群公告 |
| 日历 | 创建日程 + 读取日历 |
| 审批 | 查看、创建、撤回、转交审批 |
| 审批 | 处理审批任务 |
| 通讯录 | 读取用户基本信息 |
| 通讯录 | 读取用户高级信息（员工类型） |
| 云空间 | 查看、评论、下载和编辑云空间中文件 |
| 云空间 | 查看、下载云空间文件 |
| 多维表格 | 查看、评论、编辑和管理多维表格 |
| 日历 | 管理会议室资源 |

> 技巧：后台搜不到时换短关键词，如搜「消息」而非「获取与发送群组消息」

---

## 4. 启用应用能力（机器人 + 长连接）

### 4.1 开通机器人能力

进入应用 → **添加应用能力** → 找到 **机器人** → 开通。

> ⚠️ 不开机器人，所有发消息接口必挂，无论权限开没开。

### 4.2 长连接（卡片交互）

本系统使用 **WebSocket 长连接** 接收催办卡片的按钮点击回调，**飞书后台无需额外配置事件订阅**，代码启动后自动连飞书网关。

对应代码：`server/src/feishu/long_connection.py`，注册了 3 类事件：
- 卡片动作回调（催办的已完成/求助/延期按钮）
- 接收消息（预留）
- 消息已读（预留）

---

## 5. 设置应用可见范围

进入应用 → **版本管理与发布** → 上方「通用能力 → 可见范围」→ 编辑：

- **推荐：全部成员可见**（机器人能给任何人发消息、催办）
- 或 指定部门/成员（只给 Admin 组用）

⚠️ 若设为「仅开发者可见」，给范围外的用户发消息会报 `receive_id not found`，看起来像 ID 写错。

---

## 6. 发布应用版本

每次改完权限、安全设置、机器人、可见范围后都必须：

1. **版本管理与发布** → **创建版本**
   - 版本号：`1.0.0` 起递增
   - 更新说明：写清楚改了什么
   - 能否被搜到：✅ 开
2. **保存 → 提交审核**（测试企业可直接点发布）
3. 非超级管理员需要飞书管理后台审核通过才生效

> 口诀：**开权限 → 建版本 → 发布**，三步缺一不可。

---

## 7. 飞书侧准备工作

### 7.1 Admin 双重鉴权判定要素

服务端 `server/src/core/auth.py` 判定逻辑：满足**任一**即为 Admin。
采集企业实际值填到 `server/.env`：

| 要素 | 获取位置（飞书管理后台） | env 变量 |
|------|--------------------------|----------|
| 部门 ID（`od-` 开头） | 组织架构 → 部门 → 右侧详情 | `ADMIN_DEPARTMENT_IDS`（逗号分隔多个） |
| 职级 ID（`jl-` 开头） | 成员与部门 → 职级设置 → 详情 | `ADMIN_JOB_LEVEL_IDS`（逗号分隔多个） |
| 员工类型枚举文本 | 成员字段 → 员工类型 → 设置 | `ADMIN_EMPLOYEE_TYPES`（默认 `"正式员工"` 可先不改） |

```env
# 示例：技术部/产品部 或 总监/经理 或 正式员工 → Admin
ADMIN_DEPARTMENT_IDS=od-abc123,od-def456
ADMIN_JOB_LEVEL_IDS=jl-0001,jl-0002
ADMIN_EMPLOYEE_TYPES=正式员工
AUTH_FALLBACK_MODE=relaxed   # strict=严格飞书不通直接拒；relaxed=回退本地DB
```

### 7.2 审批模板（Cost 模块必做）

Cost 模块会自动搜名称含「成本」或「报销」的审批模板：

1. 飞书 → 工作台 → 审批 → **管理后台 → 创建审批**
2. 模板名必须含关键字：`费用报销申请`、`项目成本登记` 都行
3. 表单控件建议：金额(数字)、日期、费用类型(单选)、申请人(成员)、备注、附件
4. 保存并**发布**模板

> Audit 模块直接操作现有审批，不用提前建模板。
---

## 8. 本地 .env 配置

### server/.env（复制 `server/.env.example` 改名）

```env
# ===== 基础 =====
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
SERVER_DEBUG=true
LOG_LEVEL=INFO

# ===== 数据库（默认 SQLite 零配置） =====
DB_TYPE=sqlite
DB_PATH=./data/lanshan.db
# 生产切 MySQL 取消注释下面
# DB_TYPE=mysql
# DB_HOST=localhost
# DB_PORT=3306
# DB_USER=xxx
# DB_PASSWORD=xxx
# DB_NAME=feishu_cli

# ===== 飞书（章节1/2/7采集的值） =====
FEISHU_APP_ID=cli_xxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
FEISHU_REDIRECT_URI=http://localhost:8000/api/v1/auth/callback
FEISHU_CHAT_MAPPING=
FEISHU_MEMBER_MAPPING=
DEFAULT_BITABLE_APP_TOKEN=
DEFAULT_BITABLE_TABLE_ID=
BITABLE_PROJECT_MAPPING=

# ===== Admin 判定（章节7.1） =====
ADMIN_DEPARTMENT_IDS=
ADMIN_JOB_LEVEL_IDS=
ADMIN_EMPLOYEE_TYPES=正式员工
AUTH_FALLBACK_MODE=relaxed

# ===== 客户端 Auth Token（两端必须一致） =====
AUTH_TOKEN=随机32位字符串 openssl rand -hex 16

# ===== LLM（openai/qwen/deepseek/zhipu） =====
LLM_PROVIDER=openai
LLM_API_KEY=sk-xxxxxxxxxxxx
LLM_MODEL=gpt-4o
LLM_TEMPERATURE=0.7

# ===== 简道云 CRM（可选） =====
JIANDAOYUN_API_KEY=
JIANDAOYUN_APP_ID=
JIANDAOYUN_CUSTOMER_ENTRY_ID=
JIANDAOYUN_FIELD_MAPPING=
```

### client/.env（复制 `client/.env.example` 改名）

```env
SERVER_URL=http://localhost:8000
AUTH_TOKEN=和 server 完全相同的字符串

USER_NAME=张三
USER_ROLE=member
USER_OPEN_ID=ou_xxxxxxxxxxxx
```

---

## 9. OAuth Scope 清单（代码内置）

已在 `server/src/services/auth_service.py` 的 `DEFAULT_SCOPES` 定义，`auth login` 时自动请求：

| OAuth Scope |
|-------------|
| `calendar:readonly/write` |
| `task:task` + `task:task:writeonly` |
| `contact:readonly` |
| `docs:readonly` |
| `drive:readonly` |
| `im:message` + `im:message:write` |
| `bitable:app:readonly` + `bitable:app` |
| `base:table:read` + `base:record:retrieve/read` |
| `offline_access` |
