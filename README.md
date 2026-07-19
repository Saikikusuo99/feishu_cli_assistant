# 飞书办公自动化 CLI 助手 (Feishu CLI Assistant)

**基于 LLM 大模型的企业级飞书办公自动化系统**

## 目录 (Table of Contents)

1. [项目简介](#1-项目简介)
2. [技术架构](#2-技术架构)
3. [核心功能模块](#3-核心功能模块)
   - [3.1 基础办公指令集](#31-基础办公指令集)
   - [3.2 管理员效率工具](#32-管理员效率工具)
   - [3.3 高级功能扩展 ⭐](#33-高级功能扩展-)
4. [技术挑战与实现要点](#4-技术挑战与实现要点)
5. [快速开始 (Quick Start)](#5-快速开始-quick-start)
6. [指令大全 (Command List)](#6-指令大全-command-list)
7. [飞书应用权限配置](#7-飞书应用权限配置)
8. [项目结构说明](#8-项目结构说明)
9. [配置说明 (Configuration)](#9-配置说明-configuration)
10. [架构演进方向](#10-架构演进方向)

---

## 1. 项目简介

**Feishu CLI Assistant** 是一套基于 [飞书开放平台](https://open.feishu.cn/) 与 LLM 大模型开发的办公自动化系统，通过 CLI 终端输入自然语言命令，由 AI 自动调用飞书 API 完成任务管理、会议安排、文档摘要拆解、项目进度追踪、审批处理、AI 任务自动执行等企业办公场景，将日常重复性办公操作交给 AI 自动化处理，显著提升团队协作效率。

### 核心特性

- **🤖 AI 赋能**：集成 Qwen / OpenAI / DeepSeek / Zhipu 等多模型，支持自然语言创建任务、智能会议排期、文档 AI 摘要、任务目标拆解
- **⚡ AI 任务自动执行引擎 ⭐**：一句话目标 → 5 步全自动（AI拆解→建群→建多维表格进度→发公告→建问卷），零手动操作
- **📊 多模态呈现**：飞书 Message Card 2.0 精美卡片 + VChart 原生饼图可视化，结果不再是纯文本
- **🔐 混合权限模型**：三层防护 + 双重鉴权（飞书通讯录层级 + 本地 CLI 角色）+ strict/relaxed 高可用降级策略
- **📋 长链路状态机管理**：7 状态 ExecutionPlan 状态机，支持分步出错追踪、资源回滚提示、断点续跑、人工确认介入
- **🗂️ 多维表格深度集成**：自然语言字段自动映射（姓名→open_id、日期→毫秒时间戳），批量记录写入，权限精细控制
- **🔄 飞书双重身份管理**：tenant_access_token（应用身份）与 user_access_token（用户身份）智能切换 + 401 自动刷新重试
- **📈 项目追踪周报自动化**：自动抓取多维表格数据 → 生成任务状态统计 → 可视化卡片 + 文字报告发送
- **🎨 终端交互体验**：Click 命令行框架 + Rich 彩色输出/表格/卡片，交互友好，Windows UTF-8 编码兼容
- **🔌 可扩展架构**：C/S 四层分层架构，Service / API / AI / Feishu / DB 独立解耦，新增模块零侵入

### 技术栈

| 分类 | 技术 |
|---|---|
| **客户端框架** | Python + Click + Rich + httpx |
| **服务端框架** | FastAPI + Uvicorn（异步 ASGI） |
| **编程语言** | Python 3.10+ |
| **数据库** | SQLAlchemy 2.0 Async（支持 MySQL / SQLite 双引擎切换） |
| **AI 能力** | httpx.AsyncClient 自建封装（兼容 Qwen / OpenAI / DeepSeek / Zhipu，策略模式切换） |
| **飞书集成** | 自研 FeishuClient（双重身份 token 管理 + 自动刷新 + 多维表格/卡片封装） |
| **配置管理** | pydantic-settings（.env 环境变量加载 + 类型校验 + 单例模式） |
| **日志** | Loguru（结构化日志 + 文件切割） |
| **认证鉴权** | FastAPI Depends 依赖注入（双重角色鉴权 + 中间件模式） |

---

## 2. 技术架构

### 系统架构图

项目采用 **Client / Server（C/S）四层分层架构**，实现用户交互、接口路由、业务逻辑、基础设施的完全解耦：

```
┌─────────────────────────────────────────────────────────────┐
│ ① CLI 客户端层 (client/)                                    │
│                                                             │
│  入口：client/main.py → src/cli/app.py                      │
│  • Click：命令/子命令注册、参数解析、帮助文档                 │
│  • Rich：彩色输出、任务表格、进度渲染、Emoji                  │
│  • httpx：异步 HTTP 转发请求至服务端                         │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP / RESTful API
                            │ Header: Authorization + X-User-Open-Id
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ ② API 层 (server/src/api/v1/)                               │
│                                                             │
│  入口：server/src/app.py → 注册 12 个模块路由                │
│  • auth_middleware：全局 Token 鉴权 + CORS 跨域              │
│  • Depends(get_current_user_v2)：用户身份识别                │
│  • Depends(require_admin / require_member)：双重角色鉴权     │
│  • FastAPI 自动参数校验 + Swagger UI (/docs)                │
│  todo / meet / brief / track / cost / audit                 │
│  execute / remind / form / crm / auth / admin               │
└───────────────────────────┬─────────────────────────────────┘
                            │ 依赖注入 DB Session + 当前用户
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ ③ Service 业务逻辑层 (server/src/services/)                 │
│                                                             │
│  每个模块一个 Service 文件（与 API 层一一对应）：             │
│  • execute_service：★ AI 任务拆解引擎（5 步流程 + 状态机）  │
│  • track_service：进度数据聚合 + 饼图卡片生成                │
│  • brief_service：文档阅读 + 摘要 + 任务拆解                 │
│  • meet_service：忙闲查询 + 日历创建 + 邀请发送              │
│  • todo_service：自然语言 → 飞书任务创建                     │
│  • auth_service：OAuth 授权码模式登录 + Token 刷新          │
└───────────────────────────┬─────────────────────────────────┘
                            │ 调用基础设施
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ ④ 基础设施层                                                │
│                                                             │
│  ┌─ AI 层 (server/src/ai/) ─────────────────────────────┐   │
│  │ llm.py     → 多模型策略切换（PROVIDER_CONFIG）       │   │
│  │ prompt.py  → 任务拆解/公告/问卷 Prompt 模板 + JSON    │   │
│  │              容错解析 parse_ai_json()                 │   │
│  ├─ Feishu API 层 (server/src/feishu/) ────────────────┤   │
│  │ client.py  → 双重身份 token 管理 + 401 自动刷新      │   │
│  │              多维表格/群聊/日历/卡片 SDK 封装         │   │
│  ├─ DB 层 (server/src/db/) ────────────────────────────┤   │
│  │ base.py    → SQLAlchemy AsyncEngine + 建表           │   │
│  │ session.py → AsyncSession 会话工厂 + 连接池          │   │
│  │              (pool_size/max_overflow/pool_pre_ping)  │   │
│  │ models/    → User 模型（open_id/role/通讯录字段）    │   │
│  └─ Core 层 (server/src/core/) ────────────────────────┘   │
│    config.py → ServerConfig 单例模式加载 .env               │
│    auth.py   → 三层防护 + 双重角色鉴权 + 降级策略            │
│    logging.py → Loguru 日志配置                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 核心功能模块

### 3.1 基础办公指令集

基础办公指令集提供日常办公所需的常用功能，**全员（Member）均可使用**。

| 指令 | 语法示例 | 功能描述 |
|---|---|---|
| `health` | `health` | 检测服务端连通性与版本健康状态 |
| `todo` | `todo 下周一下午3点前把接口文档写完，交给张三审核` | 自然语言创建飞书任务（AI 解析截止时间 + 负责人） |
| `meet` | `meet 明天下午 2-4 点 和张三李四讨论产品方案` | AI 查询参会人闲忙 → 创建日历事件 → 发送邀请 |
| `brief` | `brief https://xxx.feishu.cn/docx/xxxxx` | AI 阅读飞书文档 → 生成摘要 → 拆解子任务写入多维表格 |
| `auth login` | `auth login` | 打开浏览器扫码完成飞书 OAuth 2.0 授权登录 |
| `auth logout` | `auth logout` | 退出登录，清除本地 Token 缓存 |
| `auth whoami` | `auth whoami` | 查看当前登录用户身份与角色 |

#### 3.1.1 基础指令交互示例

**场景一：创建任务成功**
```
ai-agent> todo "明晚6点前整理Q3客户数据并上传到共享文件夹，交给王五审核"

============================================================
  创建任务: 明晚6点前整理Q3客户数据并上传到共享文件夹，交给王五审核
============================================================
  [OK] Status: ok
     信息: 飞书任务创建成功

============================================================

```

**场景二：未登录授权，失败提示**
```
ai-agent> todo "写周报 明天下午"

============================================================
  创建任务: 写周报 明天下午
============================================================
  [X] 用户身份认证失败，请先通过授权链接进行飞书登录授权

============================================================

```

---

### 3.2 管理员效率工具

面向 **Admin 角色**（双重鉴权通过）开放的管理类功能，用于项目推进、资源统计与流程管控。

| 指令 | 语法示例 | 功能描述 |
|---|---|---|
| `track` | `track 智能客服项目` | 自动抓取多维表格数据 → 生成进度周报 + VChart 饼图卡片 + 文字报告 |
| `cost` | `cost 营销部Q3` | 多维表格费用数据统计分析（总额/分类占比/异常告警） |
| `audit` | `audit` | 拉取审批中心待审批列表 → 批量一键通过 / 拒绝（**需二次确认**） |
| `remind` | `remind 智能客服项目` | 扫描多维表格 → 检测逾期任务 → 发送飞书卡片催办消息给负责人 |
| `form` | `form https://xxx.feishu.cn/base/xxx` 或 `form 项目A` | 分析业务多维表格数据 → AI 生成统计结论与洞察 |
| `crm` | `crm 客户 阿里巴巴` / `crm 商机 赢单` | 对接简道云 CRM → 支持客户/商机/合同数据检索与导出 |
| `admin users` | `admin users` | 查看所有已注册用户列表（open_id / name / role / 部门） |
| `admin set-role` | `admin set-role ou_xxx admin` | 设置/修改指定用户的 CLI 角色（admin/member） |

---

### 3.3 高级功能扩展 ⭐

#### 3.3.1 AI 任务自动执行引擎（`execute` 指令）

**功能概述**：本项目最核心、最体现 AI 工程化落地能力的模块。用户只需输入一句话目标，系统自动完成 **AI 任务拆解 → 建筹备群 → 建多维表格进度跟踪 → 发布活动公告 → 创建报名问卷** 共 5 步闭环操作，全程零手动配置。

##### 核心特性

| 特性 | 说明 | 适用场景 |
|---|---|---|
| **AI 结构化拆解** | Prompt Engineering + 容错 JSON 解析，把一句话目标拆成任务列表+成员+问卷字段 | 活动筹备、项目启动、会议组织 |
| **ExecutionPlan 状态机** | 7 种状态（pending/confirmed/running/completed/failed/paused/cancelled）+ 合法转移校验 | 长链路任务的分步出错管理 |
| **资源追踪 + 手动补偿** | created_resources 记录每一步产物（群/多维表格/问卷链接），失败时返回资源列表提示用户手动清理 | 中途失败避免资源失联 |
| **断点续跑** | current_step 记录完成进度，失败重跑自动跳过已完成步骤 | 避免重复建群/建表导致重复资源 |
| **人工确认介入** | AI 拆解后先展示任务表格给用户 y/n 确认，确认后才真正执行飞书 API | 给用户"反悔权"，避免 AI 误执行 |
| **未找到成员降级** | 按姓名搜索飞书 open_id，找不到记入 unfound_members，不阻断流程 | 真实姓名有别名/同音字时保证流程可用 |
| **公告失败降级** | 群公告 API 失败（docx 群不支持公告）自动降级为发送卡片消息 | 多场景鲁棒性 |

##### 完整执行流程（5 步闭环）

```
用户输入：/execute "我们要举办一场 50 人的线下技术沙龙，邀请全公司技术部参加"
            │
            ▼
┌─────────────────────────────────────────────────────────┐
│ Step 1：AI 任务拆解（decompose_task）                   │
│  • 调用 LLM（TASK_DECOMPOSE_PROMPT）                     │
│  • parse_ai_json() 容错解析输出 JSON                     │
│  • 生成 ExecutionPlan，状态 = pending                    │
│  • 返回 CLI：结构化任务列表表格 + 成员 + 问卷建议         │
└──────────────────────────┬──────────────────────────────┘
                           │  用户 y/n 确认（confirm()）
                           ▼
┌─────────────────────────────────────────────────────────┐
│ Step 2：创建筹备群（_execute_step2_create_chat）         │
│  • 群名：{goal_name}筹备群                               │
│  • 成员姓名 → search_users API → open_id                 │
│  • 调用 group_create 创建群聊                            │
│  • add_resource("chat", chat_id) 记录 chat_id           │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│ Step 3：建多维表格并写入任务                             │
│  • 按模板建 app → 建 table → 建字段 → 批量写 records    │
│  • 字段映射：负责人→open_id、日期→毫秒时间戳             │
│  • 设置群成员 full_access 可编辑权限                    │
│  • 发送多维表格链接卡片到群聊                            │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│ Step 4：发布活动公告（_execute_step4_announcement）     │
│  • AI 生成活动策划草案（ACTIVITY_DRAFT_PROMPT）         │
│  • 优先 set_chat_announcement API                       │
│  • 失败降级 → send_chat_card 发送卡片消息                │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│ Step 5（可选）：创建报名问卷                              │
│  • AI 分析需要的问卷字段（SURVEY_FIELDS_PROMPT）        │
│  • 建多维表格（字段类型映射：Text=1/SingleSelect=3/...） │
│  • bitable_create_form_view 创建表单视图 → 问卷链接      │
│  • 发送问卷卡片到群聊                                    │
└──────────────────────────┬──────────────────────────────┘
                           ▼
                最终响应：全部创建资源链接清单
```

##### 效果演示

**场景 A：举办线下技术沙龙（完整成功流程）**
```
ai-agent> execute "我们要举办一场50人的线下技术沙龙，邀请全公司技术部参加"

✅ 任务拆解完成，以下任务将写入多维表格「需求」字段：
   活动名称: 线下技术沙龙筹备
   摘要: 面向公司技术部的 50 人线下技术分享会，含签到、主题分享、茶歇、抽奖环节

   序号   需求内容               优先级   状态    
   ────── ──────────────────── ──────── ────────
   1      确定场地与时间          P0       未开始
   2      邀请讲师确认主题        P0       未开始
   3      活动海报设计与宣传      P1       未开始
   4      报名签到系统搭建        P1       未开始
   5      现场茶歇与抽奖准备      P2       未开始

   📋 检测到需要创建问卷

❓ 是否同意该计划？ [Y/n]: y

▶️ 开始执行计划...
（HTTP 阻塞等待服务端 5 步执行完成，中间无分步进度输出）

🎉 执行计划全部完成！
┌───────────────────────────────────────────────────────┐
│ 执行结果:                                              │
│  ├── 群聊: 线下技术沙龙筹备群
│  ├── 进度表格: https://xxx.feishu.cn/base/bascnxxxx
│  ├── 活动公告: 已发布
│  └── 报名问卷: https://xxx.feishu.cn/share/base/form/viwxxxx
├───────────────────────────────────────────────────────┤
│ 多维表格已写入 5 条任务记录
│ 群成员已自动获得表格编辑权限
└───────────────────────────────────────────────────────┘
```

**场景 B：用户输入 n 取消执行**
```
❓ 是否同意该计划？ [Y/n]: n
已取消执行。
```

**场景 C：拆解阶段失败（如 LLM 超时或返回非法 JSON）**
```
ai-agent> execute "组织一场团建"

❌ 任务拆解失败: 大模型调用超时，请稍后重试
```

**场景 D：执行阶段中途失败（服务端第 2 步建群成功、第 3 步建多维表格被飞书限流 429）**
```
❓ 是否同意该计划？ [Y/n]: y

▶️ 开始执行计划...

❌ 执行失败: 创建多维表格时飞书限流（429 Too Many Requests）
⚠️ 已创建资源:
   ├── chat: 线下技术沙龙筹备群 (id: oc_xxxx)
```

##### 如何验证 execute 功能

**方法 1：查看 CLI 交互输出**
- 拆解成功：先显示「✅ 任务拆解完成...」+ 活动名称 + 摘要 + **4 列任务表格**（序号/需求内容/优先级/状态=未开始）
- `has_survey=true` 时额外显示一行：`📋 检测到需要创建问卷`
- 进入 `click.confirm` 交互：`❓ 是否同意该计划？ [Y/n]: `
- 输入 y 后仅输出一行「▶️ 开始执行计划...」，随后 HTTP 阻塞等待（**中间无分步进度输出**）
- 成功：显示「🎉 执行计划全部完成！」+ ASCII 结果框（群聊/进度表/公告/问卷 + 写入记录数）
- 失败：显示「❌ 执行失败: xxx」+「⚠️ 已创建资源:」+ 树形列出每个已成功创建资源的 type/name/id
- 用户输入 n 取消：直接输出「已取消执行。」

**方法 2：查看飞书端实际产物**
1. 确认是否创建了名称匹配的群聊，自己是否被加入
2. 群内是否收到 2 条卡片（进度表链接 + 报名问卷链接）
3. 群公告是否有 AI 生成的活动策划草案
4. 打开进度多维表格，应看到 5 条任务记录 + 正确的负责人（用户字段已关联飞书账号）

**方法 3：模拟异常场景验证状态机**
- **用户主动取消**：拆解后输入 `n` → CLI 打印「已取消执行。」，服务端 ExecutionPlan 进入 cancelled 终态（不调用任何飞书 API，无资源产生）
- **执行中断网 / 服务重启**：CLI 端显示「❌ 执行失败: Network Error/Connect Error」，服务端 ExecutionPlan 状态为 failed，且 `current_step` 停留在失败步骤
- **部分成功**：如场景 D 所示，失败响应中能看到 `created_resources` 列表（CLI 端以「⚠️ 已创建资源:」树形打印），用户可拿到已创建资源 ID 手动清理或补偿

##### 降噪策略配置（连接池 + 批量写入参数）

```ini
# server/.env
# ------------------- DB 连接池配置（避免并发操作耗尽连接） -------------------
DB_POOL_SIZE=10          # 常驻连接数
DB_MAX_OVERFLOW=20       # 峰值最多额外临时创建
DB_POOL_PRE_PING=True    # 取连接前探活（解决 MySQL 8h wait_timeout）

# ------------------- 飞书 API 重试/超时 -------------------
FEISHU_TIMEOUT=30        # 单次请求超时秒数
FEISHU_MAX_RETRIES=1     # 401/限流错误码自动重试次数
```

##### 常见问题

**Q1：execute 拆解后发现 AI 拆的任务不对，怎么取消？**
A：拆解后会明确询问 y/n，输入 `n` 即可取消，ExecutionPlan 会进入 `cancelled` 终态，不调用任何飞书 API，不会产生任何脏资源。

**Q2：执行中报错"找不到成员 张三的 open_id"怎么办？**
A：AI 返回的是姓名，系统会调用 search_users 模糊搜索。若因别名/同音字搜不到，会在 unfound_members 标记并跳过拉群，不会阻断其他步骤。你可以在群创建完后手动将该同事拉入群聊，或改用他的真实全名重试 execute。

**Q3：execute 失败重跑会不会重复建群/建多维表格？**
A：ExecutionPlan 用 current_step 记录进度，重跑时会自动跳过 current_step 之前的已完成步骤。如果是完全独立的第二次 execute，则会生成全新的 execution_id 和资源，此时会重复创建，建议手动清理上一次失败的资源（响应中有 created_resources 列表可点击）。

**Q4：execute 默认权限是什么？普通 member 可以用吗？**
A：execute 因为涉及批量创建资源（群/多维表格等），默认仅 admin 可用。需要双重鉴权通过：飞书通讯录层级（部门/职级/员工类型任一命中 Admin 配置）+ 本地 DB 中 user.role == "admin"。

##### 性能优化建议

1. **合理控制单次任务数量**：AI 拆解任务建议 ≤ 20 条，避免 bitable 单次批量写入 payload 超限被限流；超过 20 条可手动分批。
2. **Prompt 明确负责人全名**：Prompt 中如果能给出真实全名，search_users 的命中率会从 60%+ 提升到 95%+，减少 unfound_members 数量。
3. **复用多维表格模板**：对高频项目类型（如沙龙、培训、新产品上线），可把 TEMPLATE_FIELDS 固化成预设，避免每次重新建字段，节省 3-5 次 API 调用。
4. **监控 created_resources 数量**：失败后及时清理失联的群聊和多维表格，避免飞书端资源膨胀。

---

## 4. 技术挑战与实现要点

### 难点 1：多维表格（Bitable）复杂操作 —— Rate Limit 限流 + AI 字段精准映射

**问题**：
1. **读写频率限制**：飞书多维表格对「创建 app → 创建 table → 创建字段 → 批量写 records → 设置权限」这种连续多步 API 调用有严格 QPS 限制，处理大规模任务列表（几十条以上）时很容易触发 429/503 报错导致整个建表流程中断。
2. **AI 字段映射**：LLM 拆解出的是纯自然语言（如负责人「张三」、优先级「高」、日期「2026-08-01」），但 bitable 写 records 要求严格的编码格式：人员字段必须是 `[{id: open_id}]`、日期必须是**毫秒级时间戳**、单选/多选字段值必须和预设选项精确匹配。

**解决**：
1. **限流**：创建字段、写入 records 的步骤按批次串行化，对每条 API 调用之间做间隔控制，捕获到限流错误码后用**指数退避**重试一次；写 records 时默认单批次提交避免超出 payload 限制。
2. **字段映射**：Step3 构建了完整的映射链：
   - 负责人姓名 → 调用 `search_users` API 查 open_id，存进 `assignee_map`；"我"特殊映射为当前 `user_open_id`
   - 日期字符串 → `datetime.strptime` 解析后 `*1000` 转毫秒时间戳
   - 优先级/状态等枚举 → 映射到 bitable 字段预设选项值
   - 映射失败的字段**优雅降级跳过**（如日期格式无效不写入该字段），不阻断整条记录

---

### 难点 2：长链路状态机管理 —— 分步出错可回滚 + 支持人工介入

**问题**：`/execute` 是一条 5 步的长链路（拆解→建群→建多维表格→发公告→建问卷），任何一步调用飞书 API 都可能超时/失败，此时如果没有状态追踪，已成功创建的群聊/多维表格就会"失联"，用户也不知道哪一步成功了、该怎么补救；也不支持用户确认前反悔，或暂停/续跑。

**解决**：
设计了完整的 **ExecutionPlan 状态机**：

1. **7 种状态 + 合法转移校验**：`pending / confirmed / running / completed / failed / paused / cancelled`，每个转移方法（`confirm/start/pause/resume/cancel/fail`）都校验当前状态，例如"只有 pending 才能 confirm"，避免非法跳转。
2. **资源追踪**：每一步成功创建的资源（群聊 chat_id、多维表格 app_token、问卷表单链接）都通过 `plan.add_resource()` 记到 `created_resources` 列表中，最终响应里把已创建资源完整返回给 CLI，用户即使后续步骤失败也能拿到链接去**手动清理或补偿**。
3. **断点续跑**：用 `current_step` + `results` 记录完成进度，失败后可以根据 `current_step` 跳过已成功的步骤重跑，避免重复建表/建群造成浪费。
4. **人工确认环节**：AI 拆解结果生成后状态为 `pending`，CLI 端会展示任务表格并要求用户 y/n 确认，确认（`confirm()`）后才进入 running 开始实际执行，给用户"反悔权"。

---

### 难点 3：多模态反馈 —— 用飞书卡片（Message Card）精美呈现 AI 结果

**问题**：纯文本输出进度报告/统计结论很难阅读，用户也习惯了飞书中的富媒体消息体验。但飞书 Message Card 是 JSON 2.0 规范，Schema 较复杂（header/body/嵌套 elements/chart 组件参数），手动拼装容易格式报错；尤其是要嵌入**可视化饼图**时，VChart 的 chart_spec 参数非常繁琐。

**解决**：
在 Service 层抽象出**卡片构造函数**，按场景封装卡片 JSON 模板，避免在业务代码里直接拼大段 JSON：

1. **项目进度周报卡片**：
   - 使用 `schema: 2.0` + `wide_screen_mode: true` 宽屏卡片
   - **紫色卡片头（template: purple）** 展示项目名 + 日期标题
   - 内嵌 `tag: chart` 原生饼图组件，`chart_spec` 按飞书 VChart 规范配置 type=pie、valueField/categoryField、右侧图例等，直观呈现「已完成/进行中/已延迟/未开始」占比
   - `tag: lark_md` 富文本展示百分比统计 + 完成率/延迟率汇总
2. **多维表格进度卡、问卷卡**（execute 模块 `_build_bitable_card` 等）：以按钮/链接形式直接跳转对应 bitable，用户点一下就能进入飞书端操作。
3. 所有对外消息**优先走卡片**，失败才降级为纯文本兜底，保证视觉体验与健壮性兼顾。

---

### 难点 4：混合权限模型 —— 飞书通讯录层级 + 自定义 CLI 角色双重鉴权

**问题**：
- 如果只靠本地数据库的 role 字段做权限，存在"离职员工本地角色未被及时清理仍能操作"的风险，也无法和企业真实的部门/职级/员工类型挂钩。
- 如果只靠飞书通讯录，又缺少灵活的本地兜底能力（比如临时把某个骨干设为管理员但他的职级不在 Admin 列表里），且飞书 API 偶尔抖动时会导致整个系统所有人都无法操作。

**解决**：
实现了**三层防护 + 双重鉴权 + 降级策略**的混合权限模型：

1. **第一层：全局 Token 中间件**：所有非白名单接口校验 `Authorization: Bearer <AUTH_TOKEN>`，保证 CLI 客户端和服务端之间的信任链路。
2. **第二层：用户身份识别**：通过 `X-User-Open-Id` 头从本地 DB 拉取用户基础信息（role / name / department_ids）。
3. **第三层：双重角色判定**（以 require_admin 为例）：
   - ① **飞书侧**：调用通讯录接口检查用户的 `部门ID ∈ ADMIN_DEPARTMENT_IDS` 或 `职级ID ∈ ADMIN_JOB_LEVEL_IDS` 或 `员工类型 ∈ ADMIN_EMPLOYEE_TYPES`，三者命中其一即认为在飞书层级是管理员。
   - ② **本地侧**：同时要求 `user.role == "admin"`。
   - 两侧都通过才放行。
4. **降级策略**：通过 `AUTH_FALLBACK_MODE` 配置：
   - `strict`：飞书通讯录校验失败就直接 403 拒绝
   - `relaxed`（默认）：飞书 API 不可用时**自动降级为仅检查本地角色**，保证核心功能可用；但飞书 API 成功且明确判定不是 Admin 时始终拒绝，避免降级被滥用。

这样既绑定了企业真实组织架构（安全基线），又保留了本地 `admin set-role` 的灵活管理能力，还对飞书侧抖动做了高可用兜底。

---

## 5. 快速开始 (Quick Start)

### 5.1 环境要求

- Python 3.10+（建议 3.10.8 及以上）
- 飞书开放平台**自建应用**（需提前获取 App ID / App Secret，权限清单见 [飞书配置细则](https://github.com/Saikikusuo99/feishu_cli_assistant/blob/main/FEISHU_PERMISSIONS.md)）
- 可选：MySQL 5.7+ / 8.0（不配置则默认使用 SQLite 单文件数据库）

### 5.2 安装依赖

```powershell
# ============ 服务端 ============
cd server
pip install -r requirements.txt

# ============ 客户端 ============
# 新终端窗口
cd client
pip install -r requirements.txt
```

也可双击项目根目录的 `start_server.bat` / `start_client.bat`（需提前装好依赖）。

### 5.3 配置环境变量

参考各目录下的 `.env.example` 复制出 `.env` 并填入真实值：

```ini
; server/.env
APP_ID=cli_xxx
APP_SECRET=xxx
ENCRYPT_KEY=xxx         ; 飞书事件订阅加密 Key（可选）
VERIFICATION_TOKEN=xxx  ; 飞书事件订阅校验 Token（可选）

; -------- LLM 配置（4 选 1 填对应供应商 Key） --------
LLM_PROVIDER=qwen        ; qwen / openai / deepseek / zhipu
LLM_MODEL=qwen-plus
DASHSCOPE_API_KEY=sk-xxx ; Qwen 的 Key；其他供应商对应填 OPENAI_API_KEY / DEEPSEEK_API_KEY / ZHIPU_API_KEY

; -------- 数据库配置（不填则默认 SQLite） --------
DB_URL=mysql+aiomysql://user:password@localhost:3306/feishu_cli?charset=utf8mb4

; -------- 鉴权配置 --------
AUTH_TOKEN=dev_token_123                ; CLI <-> 服务端信任 Token
AUTH_FALLBACK_MODE=relaxed              ; strict / relaxed
ADMIN_DEPARTMENT_IDS=od-xxxxx1,od-xxxxx2 ; 飞书部门 ID（可多个逗号分隔）
ADMIN_JOB_LEVEL_IDS=                    ; 飞书职级 ID
ADMIN_EMPLOYEE_TYPES=1,2                ; 飞书员工类型编码
```

```ini
; client/.env
SERVER_URL=http://localhost:8000
AUTH_TOKEN=dev_token_123  ; 需与 server/.env 的 AUTH_TOKEN 一致
```

### 5.4 启动与首次登录

```powershell
# 终端 1：启动服务端（默认监听 0.0.0.0:8000）
cd server
python main.py
# 启动成功后可访问 http://localhost:8000/docs 查看 FastAPI Swagger 文档

# 终端 2：启动客户端（进入交互式 CLI）
cd client
python main.py

# 首次登录（客户端内）：
📢 请输入命令: auth login
# → 自动打开浏览器 → 扫码飞书授权 → 回到 CLI 显示「登录成功：张三（member）」
# 若要测试管理员功能，先用 admin set-role 升级为 admin
```

---

## 6. 指令大全 (Command List)

### 6.1 全员指令（require_member）

| 指令 | 参数形式 | 权限 | 说明 |
|---|---|---|---|
| `health` | 无 | Member | 健康检查，返回服务端版本 + 运行状态 + 当前时间 |
| `todo <content>` | 自然语言一句话 | Member | AI 解析后创建飞书任务（含截止时间、优先级、负责人） |
| `meet <desc>` | 描述（时间+人员+主题） | Member | 查询参会人闲忙 → 创建日历事件 → 发送邀请 |
| `brief <doc_url>` | 飞书文档 URL（docx/wiki） | Member | AI 阅读全文 → 生成摘要 → 拆解子任务写入多维表格 |
| `auth login` | 无 | Member | 飞书 OAuth 扫码授权登录（授权码模式） |
| `auth logout` | 无 | Member | 清除本地缓存 Token，退出登录 |
| `auth whoami` | 无 | Member | 查看当前用户 open_id / name / role / 部门 |

### 6.2 管理员指令（require_admin）

| 指令 | 参数形式 | 权限 | 说明 |
|---|---|---|---|
| `track [project_name]` | 项目名（可选） | Admin | 抓取多维表格数据 → 饼图卡片 + 文字报告 |
| `cost [project]` | 项目名 / 部门名 | Admin | 费用多维表格统计分析 |
| `audit` | 无（交互式选择） | Admin | 拉取待审批列表 → 一键通过/拒绝，批量操作需 y/n 二次确认 |
| `execute <goal>` | 一句话目标描述 | Admin | **AI 任务拆解引擎核心**（5 步全自动流程） |
| `remind [project]` | 项目名（可选） | Admin | 扫描逾期任务 → 给负责人发催办卡片 |
| `form <input>` | 项目名 或 bitable URL | Admin | 多维表格数据分析 + AI 洞察结论 |
| `crm <type> <keyword>` | type: customer/contract/deal + 关键词 | Admin | 简道云 CRM 数据检索（支持客户/商机/合同） |
| `admin users` | 无 | Admin | 列出全部注册用户（open_id、姓名、部门、角色） |
| `admin set-role <open_id> <role>` | open_id + role（admin/member） | Admin | 设置/修改指定用户 CLI 角色，**即时生效** |

---

## 7. 飞书应用权限配置

本项目依赖的飞书开放平台权限较多，核心必填权限类别：

1. **通讯录**：`contact:user.base:readonly`（根据姓名搜索用户、获取部门）
2. **任务**：`task:task` / `task:task:write`（`todo` 指令读写飞书任务）
3. **日历**：`calendar:calendar` / `calendar:calendar:write`（`meet` 指令查忙闲+创建日程）
4. **消息与群组**：`im:message` / `im:message:send_as_bot` / `im:chat`（创建群聊、发卡片、发公告）
5. **云文档** / **多维表格**：`docx:document` / `sheets:spreadsheet` / `bitable:app`（`brief`/`execute` 创建多维表格、读写记录、创建表单视图）
6. **审批**：`approval:approval`（`audit` 指令拉取审批+操作）
7. **OAuth：获取用户身份**：`open_id` + `email` + `phone` 三个 open scope

---

## 8. 项目结构说明

```
feishu_cli_assistant/
├── client/                          # CLI 客户端
│   ├── main.py                      # 启动入口：Windows UTF-8 编码修复 + Click 启动
│   ├── src/
│   │   ├── cli/app.py               # Click 主命令组 + Rich 欢迎 Banner
│   │   ├── commands/                # 12 个业务命令（Click 子命令）
│   │   │   ├── todo.py / meet.py / brief.py / auth.py
│   │   │   ├── execute.py / track.py / audit.py / remind.py
│   │   │   ├── cost.py / form.py / crm.py / admin.py
│   │   └── utils/                   # 基础工具
│   │       ├── config.py            # 客户端配置（读取 client/.env）
│   │       ├── http_client.py       # httpx 封装（统一 Header 注入 / 异常处理）
│   │       └── logger.py            # 客户端日志
│   └── requirements.txt
│
├── server/                          # 服务端（FastAPI）
│   ├── main.py                      # 启动入口：uvicorn.run 启动 FastAPI 应用
│   ├── src/
│   │   ├── app.py                   # FastAPI 应用实例 + 中间件注册（auth/CORS）+ include_router
│   │   ├── api/v1/                  # RESTful 路由层（与 commands 1:1 对应）
│   │   ├── services/                # 业务逻辑层（纯 Python，不接触 HTTP）
│   │   │   ├── execute_service.py   # ★ 核心：AI 任务拆解引擎（ExecutionPlan + 5 步）
│   │   │   ├── track_service.py     # ★ 进度追踪 + _generate_progress_card 卡片构造
│   │   │   ├── brief_service.py / todo_service.py / meet_service.py
│   │   │   ├── audit_service.py / remind_service.py
│   │   │   ├── cost_service.py / form_service.py / crm_service.py
│   │   │   └── auth_service.py      # OAuth 登录 + Token 刷新（含缓存）
│   │   ├── ai/                      # AI 层
│   │   │   ├── llm.py               # LLMEngine 单例 + 多模型策略切换 + 异步调用
│   │   │   └── prompt.py            # 全部 Prompt 模板 + parse_ai_json() 容错解析
│   │   ├── feishu/                  # 飞书 SDK 层
│   │   │   ├── client.py            # FeishuClient 单例 + 双重身份 + 自动刷新
│   │   │   └── long_connection.py   # 飞书长连接（事件订阅，可选）
│   │   ├── db/                      # 数据库层
│   │   │   ├── base.py              # AsyncEngine + 建表
│   │   │   ├── session.py           # get_db_session 依赖 + 连接池配置
│   │   │   └── models/user.py       # User 模型（open_id/name/role/通讯录字段）
│   │   └── core/                    # 核心横切组件
│   │       ├── config.py            # ServerConfig 单例（.env 加载 + 类型校验）
│   │       ├── auth.py              # ★ 三层防护 + 双重角色鉴权 + 降级策略
│   │       └── logging.py           # Loguru 配置（控制台 + 文件切割）
│   ├── migrations/                  # SQL 迁移脚本（用户表扩展字段）
│   └── requirements.txt
│
├── bitable_template/                # 多维表格模板定义与初始化工具
├── start_server.bat                 # Windows 一键启动服务端
├── start_client.bat                 # Windows 一键启动客户端
├── FEISHU_PERMISSIONS.md            # 飞书应用权限完整清单
└── README.md
```

---

## 9. 配置说明 (Configuration)

所有配置均通过 `.env` 环境变量管理（参考 `.env.example`），使用 `pydantic-settings` 做类型校验，ServerConfig 采用**单例模式**全局唯一。

### 9.1 服务端核心配置（server/.env）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `APP_ID` | str | 必填 | 飞书自建应用 App ID |
| `APP_SECRET` | str | 必填 | 飞书自建应用 App Secret |
| `ENCRYPT_KEY` | str | 空 | 飞书事件订阅 AES 加密 Key（事件订阅才要） |
| `VERIFICATION_TOKEN` | str | 空 | 飞书事件订阅验证 Token |
| `FEISHU_BASE_URL` | str | `https://open.feishu.cn/open-apis` | 飞书开放平台 base_url（私有化部署可改） |
| `FEISHU_TIMEOUT` | int | `30` | 飞书 API 单次请求超时（秒） |
| `LLM_PROVIDER` | str | `qwen` | LLM 供应商：qwen/openai/deepseek/zhipu（策略模式自动切换） |
| `LLM_MODEL` | str | 必填 | 模型名，如 `qwen-plus` / `gpt-4o-mini` / `deepseek-chat` |
| `LLM_TIMEOUT` | int | `60` | LLM 调用超时（大模型响应慢，建议 60-120） |
| `<供应商>_API_KEY` | str | 必填 | 对供应商对应 Key 环境变量名：`DASHSCOPE_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `ZHIPU_API_KEY` |
| `DB_URL` | str | `sqlite+aiosqlite:///./feishu_cli.db` | 数据库 URL，MySQL 写法：`mysql+aiomysql://user:pwd@host:3306/db?charset=utf8mb4` |
| `DB_POOL_SIZE` | int | `10` | 连接池常驻连接数 |
| `DB_MAX_OVERFLOW` | int | `20` | 峰值额外临时连接数 |
| `DB_POOL_PRE_PING` | bool | `True` | 取连接前探活（解决 MySQL wait_timeout 断连） |
| `AUTH_TOKEN` | str | `dev_token` | CLI <-> 服务端全局信任 Token（生产必须改强随机） |
| `AUTH_FALLBACK_MODE` | str | `relaxed` | `strict`：飞书不可用直接拒绝；`relaxed`：降级本地角色 |
| `ADMIN_DEPARTMENT_IDS` | str | 空 | 飞书 Admin 部门 ID，多个逗号分隔 |
| `ADMIN_JOB_LEVEL_IDS` | str | 空 | 飞书 Admin 职级 ID，多个逗号分隔 |
| `ADMIN_EMPLOYEE_TYPES` | str | 空 | 飞书 Admin 员工类型编码，多个逗号分隔 |
| `SERVER_HOST` | str | `0.0.0.0` | 服务端监听地址 |
| `SERVER_PORT` | int | `8000` | 服务端监听端口 |
| `LOG_LEVEL` | str | `INFO` | DEBUG/INFO/WARNING/ERROR |
| `DEFAULT_BITABLE_APP_TOKEN` | str | 空 | 默认项目多维表格 app_token |
| `BITABLE_PROJECT_MAPPING` | str | 空 | 项目名→多维表格映射（JSON 字符串） |
| `JIANDaoYun_APP_ID` / `JIANDaoYUN_API_KEY` | str | 空 | 简道云 CRM 集成（可选） |

### 9.2 客户端配置（client/.env）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `SERVER_URL` | str | `http://localhost:8000` | 服务端地址（生产改 HTTPS 域名） |
| `AUTH_TOKEN` | str | `dev_token` | 必须与服务端 AUTH_TOKEN 一致 |
| `HTTP_TIMEOUT` | int | `120` | HTTP 请求超时（execute 需要长超时） |
| `LOCALE` | str | `zh_CN` | 语言（目前仅支持简体中文） |

---

## 10. 架构演进方向

结合现有分层解耦架构设计，项目后续将在以下 4 个方向持续演进：

| 演进方向 | 核心要点 |
|---|---|
| **可靠性深化** | ExecutionPlan 持久化落地 DB + 逆序补偿回滚 + 飞书调用指数退避重试与幂等键 |
| **AI 体验增强** | SSE 流式长链路分步进度可视化 + LLM 拆解结果缓存 + AI 解析信息 Rich 结构化呈现 |
| **安全与可观测体系** | JWT 身份轮转 / 密钥配置中心统一管理 / trace_id 全链路 + Prometheus 指标 + 飞书告警 |
| **交付生态扩展** | 新增飞书 IM 机器人零端侧入口 + Docker Compose 一键部署 + GitHub Actions CI/CD 流水线 |

> 以上方向均基于现有分层解耦架构可平滑落地。


