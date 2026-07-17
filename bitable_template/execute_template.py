"""
基于模板创建多维表格并发送机器人通知

模板来源: https://ecnp67jgx129.feishu.cn/base/BDMXbcCDYaH8NZs0uhOcxo2pnIb
模板结构:
  - 表名: 研发人力
  - 字段:
    1. 需求 (Text, 主键)
    2. 优先级 (SingleSelect: P0/P1/P2/P3)
    3. 状态 (SingleSelect: 未开始/进行中/已完成)
    4. 开始时间 (DateTime, yyyy/MM/dd)
    5. 截止时间 (DateTime, yyyy/MM/dd)
    6. 负责人员 (User, 多选)
"""

import asyncio
import httpx
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'server', '.env'))

APP_ID = os.getenv('FEISHU_APP_ID')
APP_SECRET = os.getenv('FEISHU_APP_SECRET')
BASE_URL = 'https://open.feishu.cn/open-apis'

# ============================================================
# 模板字段定义
# ============================================================
TEMPLATE_TABLE_NAME = "研发人力"

TEMPLATE_FIELDS = [
    {
        "field_name": "需求",
        "type": 1,  # Text
        "ui_type": "Text",
        "is_primary": True,
    },
    {
        "field_name": "优先级",
        "type": 3,  # SingleSelect
        "ui_type": "SingleSelect",
        "property": {
            "options": [
                {"name": "P0", "color": 11},
                {"name": "P1", "color": 1},
                {"name": "P2", "color": 2},
                {"name": "P3", "color": 9},
            ]
        },
    },
    {
        "field_name": "状态",
        "type": 3,  # SingleSelect
        "ui_type": "SingleSelect",
        "property": {
            "options": [
                {"name": "未开始", "color": 18},
                {"name": "进行中", "color": 13},
                {"name": "已完成", "color": 15},
            ]
        },
    },
    {
        "field_name": "开始时间",
        "type": 5,  # DateTime
        "ui_type": "DateTime",
        "property": {
            "auto_fill": False,
            "date_formatter": "yyyy/MM/dd",
        },
    },
    {
        "field_name": "截止时间",
        "type": 5,  # DateTime
        "ui_type": "DateTime",
        "property": {
            "auto_fill": False,
            "date_formatter": "yyyy/MM/dd",
        },
    },
    {
        "field_name": "负责人员",
        "type": 11,  # User
        "ui_type": "User",
        "property": {
            "multiple": True,
        },
    },
]


async def get_tenant_token(client: httpx.AsyncClient) -> str:
    """获取 tenant_access_token"""
    resp = await client.post(
        f'{BASE_URL}/auth/v3/tenant_access_token/internal',
        json={'app_id': APP_ID, 'app_secret': APP_SECRET}
    )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'获取token失败: {data}')
    return data['tenant_access_token']


async def create_bitable_app(client: httpx.AsyncClient, token: str, name: str) -> str:
    """创建多维表格应用，返回 app_token"""
    resp = await client.post(
        f'{BASE_URL}/bitable/v1/apps',
        json={'name': name},
        headers={'Authorization': f'Bearer {token}'}
    )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'创建多维表格失败: {data}')
    app_token = data.get('data', {}).get('app', {}).get('app_token', '')
    print(f'✅ 创建多维表格应用成功: {name} (app_token: {app_token})')
    return app_token


async def create_table(client: httpx.AsyncClient, token: str, app_token: str, table_name: str) -> str:
    """创建数据表，返回 table_id"""
    # 先创建表，初始只包含一个默认字段
    resp = await client.post(
        f'{BASE_URL}/bitable/v1/apps/{app_token}/tables',
        json={
            'table': {
                'name': table_name,
                'default_view_name': '默认视图',
                'fields': [
                    {
                        'field_name': '需求',
                        'type': 1,
                    }
                ],
            }
        },
        headers={'Authorization': f'Bearer {token}'}
    )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'创建数据表失败: {data}')
    table_id = data.get('data', {}).get('table_id', '')
    print(f'✅ 创建数据表成功: {table_name} (table_id: {table_id})')
    return table_id


async def add_field(client: httpx.AsyncClient, token: str, app_token: str, table_id: str, field: dict):
    """添加字段"""
    body = {
        'field_name': field['field_name'],
        'type': field['type'],
    }
    if 'property' in field:
        body['property'] = field['property']

    resp = await client.post(
        f'{BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/fields',
        json=body,
        headers={'Authorization': f'Bearer {token}'}
    )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'添加字段 {field["field_name"]} 失败: {data}')
    print(f'  ✅ 添加字段: {field["field_name"]} (type={field["ui_type"]})')


async def get_user_open_id(client: httpx.AsyncClient, token: str) -> str:
    """获取当前机器人的操作者 open_id（通过获取当前用户信息）"""
    # 方式：列出所有用户，取第一个（或通过其他方式获取）
    # 这里用搜索接口尝试获取用户
    resp = await client.get(
        f'{BASE_URL}/contact/v3/users?page_size=5',
        headers={'Authorization': f'Bearer {token}'}
    )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'获取用户列表失败: {data}')
    users = data.get('data', {}).get('items', [])
    if users:
        return users[0].get('open_id', '')
    return ''


async def send_bot_message(client: httpx.AsyncClient, token: str, open_id: str, app_token: str, app_name: str):
    """通过机器人发送消息"""
    url = f'https://ecnp67jgx129.feishu.cn/base/{app_token}'
    content = json.dumps({
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📊 多维表格已创建"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{app_name}** 已基于模板创建完成，包含以下字段：\n\n"
                              f"- 📝 需求（文本，主键）\n"
                              f"- 🔴 优先级（P0/P1/P2/P3）\n"
                              f"- 📌 状态（未开始/进行中/已完成）\n"
                              f"- 📅 开始时间\n"
                              f"- ⏰ 截止时间\n"
                              f"- 👤 负责人员（多选）\n\n"
                              f"创建时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开表格"},
                        "type": "primary",
                        "url": url,
                    }
                ],
            },
        ],
    }, ensure_ascii=False)

    resp = await client.post(
        f'{BASE_URL}/im/v1/messages?receive_id_type=open_id',
        json={
            'receive_id': open_id,
            'msg_type': 'interactive',
            'content': content,
        },
        headers={'Authorization': f'Bearer {token}'}
    )
    data = resp.json()
    if data.get('code') != 0:
        print(f'⚠️ 发送消息失败: {data}')
    else:
        print(f'✅ 机器人消息已发送给用户 {open_id}')


async def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    app_name = f'研发人力跟踪表_{timestamp}'

    async with httpx.AsyncClient(timeout=30) as client:
        token = await get_tenant_token(client)
        print(f'🔑 Token: {token[:20]}...')

        # 1. 创建多维表格应用
        app_token = await create_bitable_app(client, token, app_name)

        # 2. 创建数据表
        table_id = await create_table(client, token, app_token, TEMPLATE_TABLE_NAME)

        # 3. 添加字段（跳过"需求"，因为它已在创建表时添加）
        for field in TEMPLATE_FIELDS:
            if field['field_name'] == '需求':
                continue  # 已在创建表时作为第一个字段
            await add_field(client, token, app_token, table_id, field)

        # 4. 输出结果
        print(f'\n{"="*60}')
        print(f'🎉 多维表格创建完成！')
        print(f'   应用名称: {app_name}')
        print(f'   表名: {TEMPLATE_TABLE_NAME}')
        print(f'   链接: https://ecnp67jgx129.feishu.cn/base/{app_token}')
        print(f'   app_token: {app_token}')
        print(f'   table_id: {table_id}')
        print(f'{"="*60}')

        # 5. 发送机器人消息
        try:
            open_id = await get_user_open_id(client, token)
            if open_id:
                await send_bot_message(client, token, open_id, app_token, app_name)
            else:
                print('⚠️ 无法获取用户 open_id，请手动发送链接')
        except Exception as e:
            print(f'⚠️ 发送机器人消息失败: {e}')

        # 返回结果供后续使用
        return {
            'ok': True,
            'app_token': app_token,
            'table_id': table_id,
            'url': f'https://ecnp67jgx129.feishu.cn/base/{app_token}',
        }


if __name__ == '__main__':
    result = asyncio.run(main())
    print(f'\n返回结果: {json.dumps(result, ensure_ascii=False, indent=2)}')