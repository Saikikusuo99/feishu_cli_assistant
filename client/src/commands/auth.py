"""OAuth认证 CLI 命令

/auth login   - 获取授权URL并完成登录（全自动）
/auth token   - 获取当前token状态
/auth logout  - 登出
"""

import click
import webbrowser
import time
from cli.app import run_async, print_result
from utils.http_client import http_client
from utils.config import client_config


@click.group(name="auth")
def auth_group():
    """OAuth认证管理"""
    pass


@auth_group.command(name="login")
def auth_login():
    """获取飞书OAuth授权URL并完成登录（全自动）

    打开浏览器完成飞书授权后，服务端自动接收回调并存储token，
    客户端通过轮询同步token到本地。
    """
    print("=" * 60)
    print("飞书OAuth授权登录（全自动模式）")
    print("=" * 60)

    result = run_async(http_client.get("/api/v1/auth/url"))
    if not result.get("ok"):
        print(f"✗ 获取授权URL失败: {result.get('error', '')}")
        return

    auth_url = result.get("auth_url", "")

    print(f"\n🔗 正在打开浏览器访问授权页面...")
    webbrowser.open(auth_url)

    print(f"⏳ 请在浏览器中完成飞书授权...")
    print(f"   （授权完成后 token 将自动同步到本地）\n")

    # 轮询等待服务端收到飞书回调并存储token（最多等120秒）
    token_ready = False
    for i in range(120):
        time.sleep(1)
        check = run_async(http_client.get("/api/v1/auth/sync_token"))
        if check.get("ok") and check.get("access_token"):
            token_ready = True
            break
        if i % 10 == 9:
            print(f"   等待授权中... ({i + 1}s)")

    if not token_ready:
        print(f"\n✗ 授权超时：未在120秒内完成飞书授权，请重试")
        return

    # 从服务端同步token
    result = run_async(http_client.get("/api/v1/auth/user"))
    if not result.get("ok"):
        print(f"\n✗ 同步失败: {result.get('msg') or result.get('error', '')}")
        return

    open_id = result.get("open_id", "")
    access_token = result.get("access_token", "")
    refresh_token = result.get("refresh_token", "")
    expire = result.get("expire", 7200)
    name = result.get("name", "")

    if not access_token:
        print(f"\n✗ 授权失败: 未获取到token")
        return

    client_config.set_user_token(open_id, access_token, refresh_token, expire)

    print("\n" + "=" * 60)
    print("✓ 授权成功！")
    print(f"  用户ID: {open_id}")
    if name:
        print(f"  用户姓名: {name}")
    print(f"  Token有效期: {expire // 3600}小时")
    print(f"  Token: {access_token[:20]}...")
    print("=" * 60)
    print("\n现在可以使用 todo/meet/brief 等命令")


@auth_group.command(name="login-simple")
def auth_login_simple():
    """简单模式登录 - 手动输入user_access_token
    
    如果授权流程遇到问题，可以直接在飞书开放平台获取user_access_token，
    然后使用此命令手动输入。
    """
    print("=" * 60)
    print("飞书用户身份登录（简单模式）")
    print("=" * 60)
    print("\n请在飞书开放平台获取user_access_token后输入：")
    print("参考路径：飞书开放平台 -> 应用 -> 调试工具 -> 获取user_access_token")
    
    access_token = input("\nuser_access_token: ").strip()
    if not access_token:
        print("✗ token为空")
        return
    
    # 获取用户信息
    print("\n正在验证token并获取用户信息...")
    result = run_async(http_client.get("/api/v1/auth/user", params={"access_token": access_token}))
    
    if result.get("ok"):
        open_id = result.get("open_id", "")
        name = result.get("name", "")
        
        # 存储token（有效期默认2小时）
        client_config.set_user_token(open_id, access_token, "", 7200)
        
        print("=" * 60)
        print("✓ 登录成功！")
        print(f"  用户ID: {open_id}")
        print(f"  用户姓名: {name}")
        print("=" * 60)
    else:
        print(f"✗ 登录失败: {result.get('msg') or result.get('error', '')}")


@auth_group.command(name="sync")
def auth_sync():
    """从服务端同步已有的用户token
    
    如果服务端已有用户授权的token，可以通过此命令同步到客户端，
    无需重新进行OAuth授权。
    """
    print("=" * 60)
    print("从服务端同步用户token")
    print("=" * 60)
    
    result = run_async(http_client.get("/api/v1/auth/user"))
    
    if result.get("ok"):
        open_id = result.get("open_id", "")
        access_token = result.get("access_token", "")
        refresh_token = result.get("refresh_token", "")
        expire = result.get("expire", 7200)
        name = result.get("name", "")
        
        if access_token:
            client_config.set_user_token(open_id, access_token, refresh_token, expire)
            
            print("=" * 60)
            print("✓ Token同步成功！")
            print(f"  用户ID: {open_id}")
            print(f"  用户姓名: {name}")
            print(f"  Token有效期: {expire // 3600}小时")
            print("=" * 60)
        else:
            print("✗ 服务端没有可用的用户token，请先使用 'auth login' 登录")
    else:
        print(f"✗ 同步失败: {result.get('msg') or result.get('error', '')}")


@auth_group.command(name="token")
def auth_token():
    """查看当前用户token状态"""
    token_info = client_config.get_user_token()
    
    if token_info:
        print("=" * 60)
        print("当前用户token状态")
        print("=" * 60)
        print(f"  用户ID: {token_info.get('open_id', '')}")
        print(f"  Token: {token_info.get('access_token', '')[:20]}...")
        print(f"  有效期至: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(token_info.get('expires_at', 0)))}")
        
        if time.time() >= token_info.get("expires_at", 0):
            print("\n ⚠️  Token已过期，请重新登录")
        else:
            print("\n ✓ Token有效")
    else:
        print("当前未登录，请使用 'auth login' 登录")


@auth_group.command(name="logout")
def auth_logout():
    """登出并清除用户token"""
    client_config.clear_user_token()
    print("✓ 已登出，用户token已清除")
