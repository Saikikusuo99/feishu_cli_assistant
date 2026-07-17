"""检查模板多维表格结构

读取用户提供的4个链接对应的表格结构，输出字段定义。
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

# 模板链接
# 链接1: base/QwDQbAWsNaolLJsJa0ocvJdQn8d  (独立base)
# 链接2-4: base/MK1fbf8m2aaPKjsQJD5cmsTVnCc 的3个表
TEMPLATE_BASE_1 = 'QwDQbAWsNaolLJsJa0ocvJdQn8d'
TEMPLATE_BASE_2 = 'MK1fbf8m2aaPKjsQJD5cmsTVnCc'

# 链接2-4指定的表
TEMPLATE_TABLES_BASE2 = [
    'tblWnVcVUkb2ozlv',  # 链接2
    'blkBWDXZNDqeMJqS',  # 链接3 (blk前缀)
    'wkfOoehyLVHTw33N',  # 链接4
]


async def get_tenant_token(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f'{BASE_URL}/auth/v3/tenant_access_token/internal',
        json={'app_id': APP_ID, 'app_secret': APP_SECRET}
    )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'获取tenant_token失败: {data}')
    return data['tenant_access_token']


def load_user_token():
    """加载用户token"""
    path = os.path.join(os.path.dirname(__file__), '..', 'server', '.user_tokens.json')
    if not os.path.exists(path):
        return None, None
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    for open_id, info in data.items():
        return info.get('access_token'), info.get('refresh_token')
    return None, None


async def refresh_user_token(client: httpx.AsyncClient, refresh_token: str):
    """刷新用户token"""
    resp = await client.post(
        f'{BASE_URL}/authen/v1/oidc/refresh_access_token',
        json={
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        },
        headers={'Authorization': f'Bearer {await get_tenant_token(client)}'}
    )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'刷新用户token失败: {data}')
    token_info = data.get('data', {})
    return token_info.get('access_token'), token_info.get('refresh_token')


async def list_tables(client: httpx.AsyncClient, token: str, app_token: str, use_user_token: bool = False):
    """列出base中的所有表"""
    headers = {'Authorization': f'Bearer {token}'}
    resp = await client.get(
        f'{BASE_URL}/bitable/v1/apps/{app_token}/tables',
        headers=headers,
        params={'page_size': 100}
    )
    data = resp.json()
    return data


async def list_fields(client: httpx.AsyncClient, token: str, app_token: str, table_id: str):
    """列出表的所有字段"""
    headers = {'Authorization': f'Bearer {token}'}
    resp = await client.get(
        f'{BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/fields',
        headers=headers,
        params={'page_size': 100}
    )
    data = resp.json()
    return data


async def list_views(client: httpx.AsyncClient, token: str, app_token: str, table_id: str):
    """列出表的所有视图"""
    headers = {'Authorization': f'Bearer {token}'}
    resp = await client.get(
        f'{BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/views',
        headers=headers,
        params={'page_size': 100}
    )
    data = resp.json()
    return data


async def inspect_base(client, token, app_token, label, specified_tables=None):
    """检查一个base的所有表结构"""
    print(f'\n{"="*70}')
    print(f'📋 检查 Base: {label} (app_token: {app_token})')
    print(f'{"="*70}')

    # 列出所有表
    tables_resp = await list_tables(client, token, app_token)
    if tables_resp.get('code') != 0:
        print(f'❌ 列出表失败: {tables_resp}')
        # 打印错误详情帮助诊断
        return None

    tables = tables_resp.get('data', {}).get('items', [])
    print(f'\n包含 {len(tables)} 个表:')
    for t in tables:
        print(f'  - {t.get("name")} (table_id: {t.get("table_id")})')

    result = {'app_token': app_token, 'tables': []}

    # 如果指定了表，只检查指定的；否则检查全部
    target_table_ids = specified_tables if specified_tables else [t['table_id'] for t in tables]

    for table in tables:
        table_id = table.get('table_id')
        if table_id not in target_table_ids:
            continue
        table_name = table.get('name')
        print(f'\n--- 表: {table_name} (table_id: {table_id}) ---')

        # 读取字段
        fields_resp = await list_fields(client, token, app_token, table_id)
        if fields_resp.get('code') != 0:
            print(f'  ❌ 读取字段失败: {fields_resp}')
            continue
        fields = fields_resp.get('data', {}).get('items', [])
        print(f'  字段数: {len(fields)}')
        for f in fields:
            print(f'    - {f.get("field_name")} | type={f.get("type")} | ui_type={f.get("ui_type")} | primary={f.get("is_primary")}')

        # 读取视图
        views_resp = await list_views(client, token, app_token, table_id)
        views = []
        if views_resp.get('code') == 0:
            views = views_resp.get('data', {}).get('items', [])
            print(f'  视图数: {len(views)}')
            for v in views:
                print(f'    - {v.get("view_name")} (type: {v.get("view_type")}, id: {v.get("view_id")})')

        result['tables'].append({
            'table_id': table_id,
            'table_name': table_name,
            'fields': fields,
            'views': views,
        })

    return result


async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        # 先获取tenant token
        tenant_token = await get_tenant_token(client)
        print(f'🔑 tenant_token: {tenant_token[:20]}...')

        # 加载用户token
        user_token, refresh_token = load_user_token()
        if user_token:
            print(f'🔑 user_token: {user_token[:20]}...')

        # 检查base1 (链接1)
        print('\n尝试用tenant_token检查Base1...')
        result1_tenant = await inspect_base(client, tenant_token, TEMPLATE_BASE_1, 'Base1(链接1)')

        # 如果tenant_token失败，尝试user_token
        if result1_tenant is None and user_token:
            print('\ntenant_token失败，尝试用user_token检查Base1...')
            # 可能需要刷新token
            result1_user = await inspect_base(client, user_token, TEMPLATE_BASE_1, 'Base1(链接1)')

        # 检查base2 (链接2-4)
        print('\n尝试用tenant_token检查Base2...')
        result2_tenant = await inspect_base(
            client, tenant_token, TEMPLATE_BASE_2,
            'Base2(链接2-4)', specified_tables=TEMPLATE_TABLES_BASE2
        )

        if result2_tenant is None and user_token:
            print('\ntenant_token失败，尝试用user_token检查Base2...')
            result2_user = await inspect_base(
                client, user_token, TEMPLATE_BASE_2,
                'Base2(链接2-4)', specified_tables=TEMPLATE_TABLES_BASE2
            )

        # 保存完整结构
        all_results = {
            'inspect_time': datetime.now().isoformat(),
            'base1': result1_tenant,
            'base2': result2_tenant,
        }
        out_path = os.path.join(os.path.dirname(__file__), 'template_structure.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f'\n💾 完整结构已保存: {out_path}')


if __name__ == '__main__':
    asyncio.run(main())
