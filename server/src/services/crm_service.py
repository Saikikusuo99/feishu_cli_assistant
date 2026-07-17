"""CRM查询服务

处理 /crm 指令的业务逻辑：
- 对接真实CRM系统（简道云）
- 未配置简道云时降级到模拟数据
- 查询客户背景信息（公司、联系人、历史记录）
- 推送信息至当前会话
- 返回客户信息摘要
"""

import logging

import httpx

from server.src.core.config import server_config
from server.src.feishu.client import feishu_client

logger = logging.getLogger("lanshan-server.crm_service")

# 简道云API基础地址
JIANDAOYUN_API_BASE = "https://api.jiandaoyun.com/api/v5"

# 默认字段映射（在 .env 中设置 JIANDAOYUN_FIELD_MAPPING 覆盖）
_DEFAULT_FIELD_MAPPING = {
    "company": "_widget_company_name",
    "industry": "_widget_industry",
    "size": "_widget_size",
    "status": "_widget_status",
    "value": "_widget_value",
    "tags": "_widget_tags",
    "contact_name": "_widget_contact_name",
    "contact_position": "_widget_contact_position",
    "contact_phone": "_widget_contact_phone",
    "contact_email": "_widget_contact_email",
    "history": "_widget_history",
    "history_date": "_widget_history_date",
    "history_type": "_widget_history_type",
    "history_desc": "_widget_history_description",
}


def _field(name: str) -> str:
    """获取字段ID，优先使用 .env 中的映射，否则用默认值"""
    return server_config.jiandaoyun_field_mapping.get(name, _DEFAULT_FIELD_MAPPING.get(name, ""))


def _is_jiandaoyun_configured() -> bool:
    """检查简道云是否已配置"""
    return bool(
        server_config.jiandaoyun_api_key
        and server_config.jiandaoyun_app_id
        and server_config.jiandaoyun_customer_entry_id
    )


async def query_customer(
    customer_name: str,
    chat_id: str | None = None,
    user_access_token: str = "",
) -> dict:
    """查询客户信息

    Args:
        customer_name: 客户名称
        chat_id: 群聊ID（用于推送信息）
        user_access_token: 用户访问令牌
    """
    try:
        if _is_jiandaoyun_configured():
            customer_data = await _fetch_from_jiandaoyun(customer_name)
        else:
            customer_data = _mock_customer_data(customer_name)
            logger.info(f"简道云未配置，使用模拟CRM数据: {customer_name}")

        if customer_data.get("ok") is False:
            return customer_data

        customer_info = customer_data.get("data", {})

        if chat_id and feishu_client.is_configured:
            summary_text = _format_customer_summary(customer_info)
            result = await feishu_client.send_message(
                receive_id=chat_id,
                content=f'{{"text":"{summary_text}"}}',
                msg_type="text",
                user_access_token=user_access_token,
            )

            if result.get("error"):
                logger.warning("推送客户信息失败")
                customer_info["message_sent"] = False
            else:
                customer_info["message_sent"] = True
                customer_info["message_id"] = result.get("data", {}).get("message_id", "")
                logger.info(f"客户信息已推送至群聊: {chat_id}")
        else:
            customer_info["message_sent"] = False

        return {
            "ok": True,
            "message": "客户信息查询成功",
            "customer_name": customer_name,
            **customer_info,
        }

    except Exception as e:
        logger.error(f"查询客户信息失败: {e}")
        return {"ok": False, "error": str(e)}


async def _fetch_from_jiandaoyun(customer_name: str) -> dict:
    """从简道云CRM表单查询客户数据

    查询客户表单中匹配的客户记录，返回标准化客户信息。
    字段映射请在文件顶部的 FIELD_* 常量区域修改。
    """
    url = f"{JIANDAOYUN_API_BASE}/app/entry/data/list"
    headers = {
        "Authorization": f"Bearer {server_config.jiandaoyun_api_key}",
        "Content-Type": "application/json",
    }

    body = {
        "app_id": server_config.jiandaoyun_app_id,
        "entry_id": server_config.jiandaoyun_customer_entry_id,
        "limit": 1,
        "filter": {
            "rel": "and",
            "cond": [
                {
                    "field": _field("company"),
                    "type": "text",
                    "method": "eq",
                    "value": [customer_name],
                }
            ],
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=body, headers=headers)

        if resp.status_code != 200:
            logger.error(f"简道云API返回错误 HTTP {resp.status_code}: {resp.text}")
            return {"ok": False, "error": f"简道云API返回错误 HTTP {resp.status_code}"}

        result = resp.json()
        data_list = result.get("data", [])

        if not data_list:
            logger.info(f"简道云中未找到客户: {customer_name}")
            return {
                "ok": True,
                "data": {
                    "company": customer_name,
                    "industry": "未录入",
                    "size": "未录入",
                    "contact": {"name": "暂无", "position": "", "phone": "", "email": ""},
                    "history": [],
                    "status": "未录入",
                    "value": "",
                    "tags": [],
                },
            }

        # 将简道云返回的原始数据映射为标准客户信息格式
        raw = data_list[0]
        customer_info = {
            "company": raw.get(_field("company"), customer_name),
            "industry": raw.get(_field("industry"), ""),
            "size": raw.get(_field("size"), ""),
            "contact": {
                "name": raw.get(_field("contact_name"), ""),
                "position": raw.get(_field("contact_position"), ""),
                "phone": raw.get(_field("contact_phone"), ""),
                "email": raw.get(_field("contact_email"), ""),
            },
            "status": raw.get(_field("status"), ""),
            "value": raw.get(_field("value"), ""),
            "tags": raw.get(_field("tags"), []) if isinstance(raw.get(_field("tags")), list) else [],
            "history": _parse_history(raw.get(_field("history"), [])),
        }

        logger.info(f"简道云查询成功: {customer_name}")
        return {"ok": True, "data": customer_info}

    except httpx.TimeoutException:
        logger.error("简道云API请求超时")
        return {"ok": False, "error": "简道云API请求超时"}
    except Exception as e:
        logger.error(f"简道云API查询失败: {e}")
        return {"ok": False, "error": f"简道云API查询失败: {e}"}


def _parse_history(history_data: list) -> list:
    """解析简道云子表单中的历史记录数据"""
    result = []
    if not history_data or not isinstance(history_data, list):
        return result
    for record in history_data:
        result.append({
            "date": record.get(_field("history_date"), ""),
            "type": record.get(_field("history_type"), ""),
            "description": record.get(_field("history_desc"), ""),
        })
    return result


def _mock_customer_data(customer_name: str) -> dict:
    """模拟客户数据（简道云未配置时使用）"""
    mock_customers = {
        "北京科技有限公司": {
            "company": "北京科技有限公司",
            "industry": "科技",
            "size": "中型企业",
            "contact": {
                "name": "张三",
                "position": "采购经理",
                "phone": "138****1234",
                "email": "zhangsan@example.com",
            },
            "history": [
                {"date": "2026-06-15", "type": "首次拜访", "description": "了解客户需求"},
                {"date": "2026-06-28", "type": "方案演示", "description": "产品方案介绍"},
                {"date": "2026-07-05", "type": "合同谈判", "description": "价格协商中"},
            ],
            "status": "跟进中",
            "value": "50万",
            "tags": ["VIP客户", "高意向"],
        },
        "上海贸易集团": {
            "company": "上海贸易集团",
            "industry": "贸易",
            "size": "大型企业",
            "contact": {
                "name": "李四",
                "position": "总监",
                "phone": "139****5678",
                "email": "lisi@example.com",
            },
            "history": [
                {"date": "2026-05-20", "type": "客户建档", "description": "建立客户档案"},
                {"date": "2026-06-10", "type": "需求调研", "description": "收集业务需求"},
            ],
            "status": "待跟进",
            "value": "200万",
            "tags": ["战略客户"],
        },
        "广州制造公司": {
            "company": "广州制造公司",
            "industry": "制造业",
            "size": "中型企业",
            "contact": {
                "name": "王五",
                "position": "技术负责人",
                "phone": "137****9012",
                "email": "wangwu@example.com",
            },
            "history": [
                {"date": "2026-07-01", "type": "技术交流", "description": "技术方案讨论"},
            ],
            "status": "初步接触",
            "value": "30万",
            "tags": ["新客户"],
        },
    }

    if customer_name in mock_customers:
        return {"ok": True, "data": mock_customers[customer_name]}

    return {
        "ok": True,
        "data": {
            "company": customer_name,
            "industry": "未知",
            "size": "未知",
            "contact": {"name": "未找到联系人", "position": "", "phone": "", "email": ""},
            "history": [],
            "status": "未找到",
            "value": "",
            "tags": [],
        },
    }


def _format_customer_summary(customer_info: dict) -> str:
    """格式化客户信息摘要"""
    summary = "**客户信息查询结果**\n\n"
    summary += f"公司名称: {customer_info.get('company', '')}\n"
    summary += f"行业: {customer_info.get('industry', '')}\n"
    summary += f"规模: {customer_info.get('size', '')}\n"
    summary += f"状态: {customer_info.get('status', '')}\n"
    summary += f"预估价值: {customer_info.get('value', '')}\n\n"

    contact = customer_info.get("contact", {})
    summary += "**联系人信息**\n"
    summary += f"姓名: {contact.get('name', '')}\n"
    summary += f"职位: {contact.get('position', '')}\n"
    summary += f"电话: {contact.get('phone', '')}\n"
    summary += f"邮箱: {contact.get('email', '')}\n\n"

    tags = customer_info.get("tags", [])
    if tags:
        summary += f"标签: {', '.join(tags)}\n\n"

    history = customer_info.get("history", [])
    if history:
        summary += "**历史记录**\n"
        for record in history:
            summary += f"- {record.get('date', '')} [{record.get('type', '')}]: {record.get('description', '')}\n"

    return summary


async def list_customers(
    status: str | None = None,
    limit: int = 10,
    user_access_token: str = "",
) -> dict:
    """获取客户列表"""
    try:
        if _is_jiandaoyun_configured():
            return await _list_from_jiandaoyun(status, limit)
        else:
            return _list_mock(status, limit)

    except Exception as e:
        logger.error(f"获取客户列表失败: {e}")
        return {"ok": False, "error": str(e)}


async def _list_from_jiandaoyun(status: str | None, limit: int) -> dict:
    """从简道云获取客户列表"""
    url = f"{JIANDAOYUN_API_BASE}/app/entry/data/list"
    headers = {
        "Authorization": f"Bearer {server_config.jiandaoyun_api_key}",
        "Content-Type": "application/json",
    }

    body = {
        "app_id": server_config.jiandaoyun_app_id,
        "entry_id": server_config.jiandaoyun_customer_entry_id,
        "limit": min(limit, 100),
        "fields": [_field("company"), _field("status"), _field("value")],
    }

    if status:
        body["filter"] = {
            "rel": "and",
            "cond": [
                {
                    "field": _field("status"),
                    "type": "text",
                    "method": "eq",
                    "value": [status],
                }
            ],
        }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=body, headers=headers)

        if resp.status_code != 200:
            logger.error(f"简道云API返回错误 HTTP {resp.status_code}")
            return {"ok": False, "error": f"简道云API返回错误 HTTP {resp.status_code}"}

        result = resp.json()
        customers = []
        for item in result.get("data", []):
            customers.append({
                "company": item.get(_field("company"), ""),
                "status": item.get(_field("status"), ""),
                "value": item.get(_field("value"), ""),
            })

        return {
            "ok": True,
            "message": f"获取到 {len(customers)} 条客户记录",
            "customers": customers,
        }

    except httpx.TimeoutException:
        logger.error("简道云API请求超时")
        return {"ok": False, "error": "简道云API请求超时"}
    except Exception as e:
        logger.error(f"简道云API查询失败: {e}")
        return {"ok": False, "error": f"简道云API查询失败: {e}"}


def _list_mock(status: str | None, limit: int) -> dict:
    """模拟客户列表"""
    mock_list = [
        {"company": "北京科技有限公司", "status": "跟进中", "value": "50万"},
        {"company": "上海贸易集团", "status": "待跟进", "value": "200万"},
        {"company": "广州制造公司", "status": "初步接触", "value": "30万"},
        {"company": "深圳电子科技", "status": "已成交", "value": "80万"},
        {"company": "杭州互联网公司", "status": "跟进中", "value": "120万"},
    ]

    if status:
        mock_list = [c for c in mock_list if c["status"] == status]

    return {
        "ok": True,
        "message": f"获取到 {len(mock_list)} 条客户记录（模拟数据）",
        "customers": mock_list[:limit],
    }