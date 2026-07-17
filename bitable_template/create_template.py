"""
基于模板创建多维表格并发送机器人通知

模板来源: base/MK1fbf8m2aaPKjsQJD5cmsTVnCc -> 任务分配表(8字段含公式)
新建一个base，包含任务分配表，作为brief功能的预设表格。
使用用户身份token创建(用户拥有所有权)，使用应用身份token发消息
"""
import asyncio
import httpx
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'server', '.env'))

APP_ID = os.getenv('FEISHU_APP_ID')
APP_SECRET = os.getenv('FEISHU_APP_SECRET')
BASE_URL = 'https://open.feishu.cn/open-apis'
FEISHU_DOMAIN = 'ecnp67jgx129.feishu.cn'

# ============================================================
# 任务分配表 (brief功能预设表格)
# ============================================================
TABLE_NAME = "任务分配"
# 普通字段(公式字段单独处理，需引用其他字段ID)
TABLE_FIELDS = [
    {
        "field_name": "任务描述",
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
                {"name": "高", "color": 11},
                {"name": "中", "color": 1},
                {"name": "低", "color": 2},
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
        "field_name": "负责人",
        "type": 11,  # User
        "ui_type": "User",
        "property": {"multiple": True},
    },
    {
        "field_name": "开始日期",
        "type": 5,  # DateTime
        "ui_type": "DateTime",
        "property": {"auto_fill": False, "date_formatter": "yyyy/MM/dd"},
    },
    {
        "field_name": "截止日期",
        "type": 5,  # DateTime
        "ui_type": "DateTime",
        "property": {"auto_fill": False, "date_formatter": "yyyy/MM/dd"},
    },
    {
        "field_name": "备注",
        "type": 1,  # Text
        "ui_type": "Text",
    },
]
# 公式字段定义(占位，formula_expression在运行时填充)
FORMULA_FIELD = {
    "field_name": "任务所需时长",
    "type": 20,  # Formula
    "ui_type": "Formula",
    "property": {"formatter": "0"},
}
# 额外视图(默认grid视图随表创建)
EXTRA_VIEWS = [
    {"view_name": "新增任务", "view_type": "form"},
    {"view_name": "负责人看板", "view_type": "kanban"},
    {"view_name": "任务进展甘特图", "view_type": "gantt"},
]


# ============================================================
# Token管理
# ============================================================
async def get_tenant_token(client: httpx.AsyncClient) -> str:
    """获取 tenant_access_token(应用身份，用于发消息)"""
    resp = await client.post(
        f'{BASE_URL}/auth/v3/tenant_access_token/internal',
        json={'app_id': APP_ID, 'app_secret': APP_SECRET}
    )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'获取tenant_token失败: {data}')
    return data['tenant_access_token']


def load_user_token():
    """从.user_tokens.json加载用户token和open_id"""
    path = os.path.join(os.path.dirname(__file__), '..', 'server', '.user_tokens.json')
    if not os.path.exists(path):
        return None, None, None
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    for open_id, info in data.items():
        return info.get('access_token'), info.get('refresh_token'), open_id
    return None, None, None


def save_user_token(access_token, refresh_token, open_id):
    """保存刷新后的用户token"""
    path = os.path.join(os.path.dirname(__file__), '..', 'server', '.user_tokens.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if open_id in data:
        data[open_id]['access_token'] = access_token
        data[open_id]['refresh_token'] = refresh_token
        data[open_id]['expires_at'] = time.time() + 7200
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def get_valid_user_token(client: httpx.AsyncClient):
    """获取有效的用户token，过期则刷新。返回 (user_token, open_id)"""
    access_token, refresh_token, open_id = load_user_token()
    if not access_token:
        raise RuntimeError('未找到用户token，请先完成OAuth授权(auth login)')

    # 先尝试当前token是否可用
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = await client.get(f'{BASE_URL}/authen/v1/user_info', headers=headers)
    data = resp.json()
    if data.get('code') == 0:
        return access_token, open_id

    # token失效，尝试刷新
    print(f'用户token失效(code={data.get("code")})，尝试刷新...')
    tenant_token = await get_tenant_token(client)
    resp = await client.post(
        f'{BASE_URL}/authen/v1/refresh_access_token',
        json={'grant_type': 'refresh_token', 'refresh_token': refresh_token},
        headers={'Authorization': f'Bearer {tenant_token}'}
    )
    rdata = resp.json()
    if rdata.get('code') != 0:
        raise RuntimeError(f'刷新用户token失败: {rdata}')
    new_access = rdata['data']['access_token']
    new_refresh = rdata['data'].get('refresh_token', refresh_token)
    save_user_token(new_access, new_refresh, open_id)
    print('用户token已刷新')
    return new_access, open_id


# ============================================================
# 多维表格操作
# ============================================================
async def create_bitable_app(client, token, name):
    """创建多维表格应用，返回 app_token"""
    resp = await client.post(
        f'{BASE_URL}/bitable/v1/apps',
        json={'name': name},
        headers={'Authorization': f'Bearer {token}'}
    )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'创建多维表格失败: {data}')
    app_token = data['data']['app']['app_token']
    print(f'[OK] 创建多维表格应用: {name} (app_token: {app_token})')
    return app_token


async def create_table(client, token, app_token, table_name, primary_field_name):
    """创建数据表(含主键字段)，返回 table_id"""
    resp = await client.post(
        f'{BASE_URL}/bitable/v1/apps/{app_token}/tables',
        json={
            'table': {
                'name': table_name,
                'default_view_name': '任务总表',
                'fields': [
                    {'field_name': primary_field_name, 'type': 1}
                ],
            }
        },
        headers={'Authorization': f'Bearer {token}'}
    )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'创建数据表 {table_name} 失败: {data}')
    table_id = data['data']['table_id']
    print(f'[OK] 创建数据表: {table_name} (table_id: {table_id})')
    return table_id


async def add_field(client, token, app_token, table_id, field):
    """添加字段，返回 field_id"""
    body = {'field_name': field['field_name'], 'type': field['type']}
    if 'ui_type' in field:
        body['ui_type'] = field['ui_type']
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
    field_id = data['data']['field']['field_id']
    print(f'  [OK] 字段: {field["field_name"]} ({field.get("ui_type", "")}) -> {field_id}')
    return field_id


async def create_view(client, token, app_token, table_id, view_name, view_type):
    """创建视图"""
    resp = await client.post(
        f'{BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/views',
        json={'view_name': view_name, 'view_type': view_type},
        headers={'Authorization': f'Bearer {token}'}
    )
    data = resp.json()
    if data.get('code') != 0:
        print(f'  [WARN] 创建视图 {view_name}({view_type}) 失败: {data.get("msg")}')
        return None
    print(f'  [OK] 视图: {view_name} ({view_type})')
    return data['data']['view']['view_id']


# ============================================================
# 机器人消息
# ============================================================
async def send_bot_message(client, tenant_token, open_id, app_token, app_name, tables_info):
    """通过机器人发送卡片消息"""
    url = f'https://{FEISHU_DOMAIN}/base/{app_token}'
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{app_name}** 已基于模板创建完成\n\n{tables_info}\n\n"
                           f"创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
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
    ]
    content = json.dumps({
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "多维表格已创建"},
            "template": "blue",
        },
        "elements": elements,
    }, ensure_ascii=False)

    resp = await client.post(
        f'{BASE_URL}/im/v1/messages?receive_id_type=open_id',
        json={'receive_id': open_id, 'msg_type': 'interactive', 'content': content},
        headers={'Authorization': f'Bearer {tenant_token}'}
    )
    data = resp.json()
    if data.get('code') != 0:
        print(f'[WARN] 发送消息失败: {data}')
    else:
        print(f'[OK] 机器人消息已发送给用户 {open_id}')


# ============================================================
# 主流程
# ============================================================
async def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    app_name = f'项目跟踪表_{timestamp}'

    async with httpx.AsyncClient(timeout=30) as client:
        # 1. 获取用户token(创建表格用)和tenant_token(发消息用)
        user_token, open_id = await get_valid_user_token(client)
        print(f'用户身份token: {user_token[:20]}... (open_id: {open_id})')

        # 2. 创建多维表格应用(用用户身份，用户拥有所有权)
        app_token = await create_bitable_app(client, user_token, app_name)

        # 3. 创建任务分配表
        print(f'\n--- 创建表: {TABLE_NAME} ---')
        table_id = await create_table(client, user_token, app_token, TABLE_NAME, TABLE_FIELDS[0]['field_name'])

        # 添加普通字段，记录开始/截止日期的field_id用于公式
        start_date_field_id = None
        end_date_field_id = None
        for field in TABLE_FIELDS[1:]:
            fid = await add_field(client, user_token, app_token, table_id, field)
            if field['field_name'] == '开始日期':
                start_date_field_id = fid
            elif field['field_name'] == '截止日期':
                end_date_field_id = fid

        # 4. 添加公式字段: 任务所需时长 = 截止日期 - 开始日期
        print(f'\n--- 添加公式字段: 任务所需时长 ---')
        formula_expr = (
            f"bitable::$table[{table_id}].$field[{end_date_field_id}]"
            f"-bitable::$table[{table_id}].$field[{start_date_field_id}]"
        )
        formula_field = dict(FORMULA_FIELD)
        formula_field['property'] = {'formatter': '0', 'formula_expression': formula_expr}
        await add_field(client, user_token, app_token, table_id, formula_field)

        # 5. 创建额外视图
        print(f'\n--- 创建视图: {TABLE_NAME} ---')
        for v in EXTRA_VIEWS:
            await create_view(client, user_token, app_token, table_id, v['view_name'], v['view_type'])

        # 6. 输出结果
        url = f'https://{FEISHU_DOMAIN}/base/{app_token}'
        print(f'\n{"="*60}')
        print(f'多维表格创建完成!')
        print(f'  应用名称: {app_name}')
        print(f'  表: {TABLE_NAME} ({table_id}) - 8字段(含公式)')
        print(f'  链接: {url}')
        print(f'{"="*60}')

        # 7. 通过机器人发送消息(用应用身份)
        tables_info = (
            f"[{TABLE_NAME}]: 任务描述/优先级/状态/负责人/开始日期/截止日期/任务所需时长(公式)/备注\n"
            f"  含视图: 任务总表、新增任务(表单)、负责人看板、甘特图"
        )
        tenant_token = await get_tenant_token(client)
        await send_bot_message(client, tenant_token, open_id, app_token, app_name, tables_info)

        return {'ok': True, 'app_token': app_token, 'url': url,
                'tables': {TABLE_NAME: table_id}}


if __name__ == '__main__':
    result = asyncio.run(main())
    print(f'\n返回结果: {json.dumps(result, ensure_ascii=False, indent=2)}')