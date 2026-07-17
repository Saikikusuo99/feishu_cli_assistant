"""成本录入服务

处理 /cost 指令的业务逻辑：
- 录入成本并触发飞书审批流程
- 自动搜索匹配的审批定义
- 支持模拟/降级模式
"""

import logging
import uuid

from server.src.feishu.client import feishu_client

logger = logging.getLogger("lanshan-server.cost_service")


async def _search_approval_code(keyword: str, user_access_token: str = "") -> str | None:
    """搜索审批定义码（自动匹配）

    优先使用应用身份获取审批定义列表（审批定义是租户级资源，非用户相关），
    如果失败则尝试用户身份。

    Args:
        keyword: 关键词（如"成本"、"报销"、"支出"等）
        user_access_token: 用户访问令牌

    Returns:
        匹配的审批定义码，未找到返回None
    """
    try:
        if not feishu_client.is_configured:
            logger.info("飞书客户端未配置，跳过审批定义搜索")
            return None

        result = await feishu_client.approval_get_definitions()
        if result.get("error") or result.get("code") != 0:
            logger.warning("应用身份获取审批定义列表失败，尝试用户身份...")
            if user_access_token:
                result = await feishu_client.approval_get_definitions(user_access_token=user_access_token)
            if result.get("error") or result.get("code") != 0:
                logger.warning("用户身份获取审批定义列表也失败")
                return None

        definitions = result.get("data", {}).get("items", [])
        if not definitions:
            logger.info("未找到任何审批定义")
            return None

        keyword_lower = keyword.lower()
        matched = []
        for d in definitions:
            name = d.get("approval_name", "").lower()
            desc = d.get("description", "").lower()
            code = d.get("approval_code", "")
            if keyword_lower in name or keyword_lower in desc:
                matched.append((name, code))

        if matched:
            logger.info(f"找到 {len(matched)} 个匹配的审批定义: {matched}")
            return matched[0][1]
        else:
            logger.info(f"未找到匹配 '{keyword}' 的审批定义")
            return None

    except Exception as e:
        logger.error(f"搜索审批定义失败: {e}")
        return None


async def create_cost_record(
    project_name: str,
    amount: float,
    category: str,
    description: str = "",
    approval_code: str | None = None,
    open_id: str = "",
    title: str = "",
    user_access_token: str = "",
) -> dict:
    """录入成本记录并触发飞书审批

    按照开发指南步骤：
    1. 获取审批定义码（未提供时自动搜索）
    2. 组装表单数据
    3. 调用创建审批实例接口
    4. 返回实例编码用于后续查询

    Args:
        project_name: 项目名称
        amount: 金额
        category: 分类（如：人力成本、物料成本、差旅费用等）
        description: 费用描述
        approval_code: 审批定义码（可选，未提供时自动搜索）
        open_id: 审批发起人open_id（应用身份时需要）
        title: 审批实例展示名称（可选）
        user_access_token: 用户访问令牌

    Returns:
        包含 instance_code、approval_url 等信息的字典
    """
    try:
        approval_instance_code = None
        approval_url = ""

        if not feishu_client.is_configured:
            return _mock_create_cost(project_name, amount, category, description, reason="飞书客户端未配置")

        if not approval_code:
            logger.info("未提供approval_code，尝试自动搜索审批定义...")
            approval_code = await _search_approval_code("成本", user_access_token=user_access_token)
            if not approval_code:
                approval_code = await _search_approval_code("报销", user_access_token=user_access_token)
            if approval_code:
                logger.info(f"自动找到审批定义码: {approval_code}")
            else:
                logger.warning("未找到匹配的审批定义，使用模拟模式")
                return _mock_create_cost(project_name, amount, category, description, reason="未找到匹配的审批定义")

        form_data = {}
        try:
            definition_detail = await feishu_client.approval_get_definition_detail(
                approval_code=approval_code
            )
            logger.debug(f"审批定义详情返回: {definition_detail}")
            if definition_detail.get("code") == 0:
                form_value = definition_detail.get("data", {}).get("form", [])
                import json as json_mod
                if isinstance(form_value, str):
                    try:
                        form_fields = json_mod.loads(form_value)
                    except Exception:
                        form_fields = []
                elif isinstance(form_value, list):
                    form_fields = form_value
                else:
                    form_fields = []
                logger.debug(f"表单字段: {form_fields}")
                form_data = _build_form_data(
                    form_fields,
                    project_name,
                    amount,
                    category,
                    description,
                )
                logger.info(f"使用审批定义表单控件构建数据: {form_data}")
            else:
                logger.warning("获取审批定义详情失败，使用默认字段名")
                form_data = _build_default_form_data(project_name, amount, category, description)
        except Exception as e:
            logger.warning(f"获取审批定义表单结构失败: {e}，使用默认字段名")
            form_data = _build_default_form_data(project_name, amount, category, description)

        if not title:
            title = f"{project_name} - {category}报销"

        approval_result = await feishu_client.approval_create(
            approval_code=approval_code,
            form_data=form_data,
            open_id=open_id,
            title=title,
            use_app_token=True,
        )

        if approval_result.get("error") or approval_result.get("code") != 0:
            error_msg = approval_result.get("msg", str(approval_result.get("error", "未知错误")))
            logger.warning(f"创建审批实例失败: {error_msg}，降级到模拟模式")
            return _mock_create_cost(project_name, amount, category, description, reason=f"API错误: {error_msg}")

        approval_instance_code = approval_result.get("data", {}).get("instance_code", "")
        approval_url = f"https://open.feishu.cn/open-apis/approval/v4/instances/{approval_instance_code}"
        logger.info(f"审批流程已触发，instance_code={approval_instance_code}")

        return {
            "ok": True,
            "message": "成本录入成功，审批流程已发起",
            "project_name": project_name,
            "amount": amount,
            "category": category,
            "description": description,
            "approval_code": approval_code,
            "instance_code": approval_instance_code,
            "approval_url": approval_url,
            "title": title,
        }

    except Exception as e:
        logger.error(f"录入成本失败: {e}")
        return {"ok": False, "error": str(e)}


def _build_form_data(
    form_fields: list,
    project_name: str,
    amount: float,
    category: str,
    description: str = "",
) -> list:
    """根据审批定义表单结构构建表单数据

    飞书审批API要求form字段为JSON数组格式：
    [{"id":"控件ID","type":"控件类型","value":"值"}, ...]

    Args:
        form_fields: 审批定义的表单字段列表（已解析的JSON）
        project_name: 项目名称
        amount: 金额
        category: 分类
        description: 事由

    Returns:
        表单数据数组（飞书API要求的格式）
    """
    result = []

    for field in form_fields:
        field_name = field.get("name", "")
        field_id = field.get("id", "")
        field_type = field.get("type", "")

        if not field_id:
            continue

        if "项目" in field_name or "名称" in field_name:
            result.append({"id": field_id, "type": field_type, "value": project_name})
        elif "金额" in field_name:
            if field_type == "number":
                value = int(amount) if amount.is_integer() else amount
            else:
                value = str(amount)
            result.append({"id": field_id, "type": field_type, "value": value})
        elif "类型" in field_name or "分类" in field_name:
            selected_value = category
            if field_type == "radioV2" or field_type == "select":
                options = field.get("option", [])
                for opt in options:
                    opt_text = opt.get("text", "")
                    opt_value = opt.get("value", "")
                    if category in opt_text or opt_text in category:
                        selected_value = opt_value
                        break
            result.append({"id": field_id, "type": field_type, "value": selected_value})
        elif "事由" in field_name or "描述" in field_name or "说明" in field_name:
            if description:
                result.append({"id": field_id, "type": field_type, "value": description})

    if not result:
        logger.warning("未匹配到任何表单控件，使用默认字段名")
        result = [
            {"id": "项目名称", "type": "input", "value": project_name},
            {"id": "金额", "type": "number", "value": str(amount)},
            {"id": "分类", "type": "input", "value": category},
        ]
        if description:
            result.append({"id": "事由", "type": "textarea", "value": description})

    return result


def _build_default_form_data(
    project_name: str,
    amount: float,
    category: str,
    description: str = "",
) -> list:
    """构建默认表单数据（数组格式，符合飞书API要求）"""
    result = [
        {"id": "项目名称", "type": "input", "value": project_name},
        {"id": "金额", "type": "number", "value": str(amount)},
        {"id": "分类", "type": "input", "value": category},
    ]
    if description:
        result.append({"id": "事由", "type": "textarea", "value": description})
    return result


def _mock_create_cost(project_name: str, amount: float, category: str, description: str, reason: str = "") -> dict:
    """模拟创建成本记录（降级模式）

    Args:
        reason: 降级原因（如API错误信息）
    """
    mock_instance_code = f"inst_mock_{uuid.uuid4().hex[:8]}"
    msg = "[模拟模式] 成本录入成功，审批流程已发起"
    if reason:
        msg += f"（降级原因：{reason}）"
    return {
        "ok": True,
        "message": msg,
        "project_name": project_name,
        "amount": amount,
        "category": category,
        "description": description,
        "approval_code": "mock_cost_approval",
        "instance_code": mock_instance_code,
        "approval_url": f"https://mock.feishu.cn/approval/{mock_instance_code}",
        "title": f"{project_name} - {category}报销",
        "mock_mode": True,
    }