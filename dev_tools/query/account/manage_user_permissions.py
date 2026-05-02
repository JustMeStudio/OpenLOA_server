import requests

# 配置
BASE_URL = "http://127.0.0.1:9000"
MANAGE_URL = f"{BASE_URL}/account/manage_permissions"

# 必须使用【管理员账号】登录后获取的 access_token
ADMIN_ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleGFtcGxlIjoiZXhhbXBsZSJ9.example"

headers = {
    "Authorization": f"Bearer {ADMIN_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# 场景 A：提升某个用户为管理员
payload_upgrade = {
    "user_id": "7be47d1a-6079-4834-a67f-369a2441d677",
    "new_role": "admin"
}

# 场景 B：禁用（封禁）某个违规账号
payload_disable = {
    "login_name": "example",
    "new_enabled": False
}

# 场景 C：同时修改角色并启用账号
payload_both = {
    "login_name": "new_staff",
    "new_role": "user",
    "new_enabled": True
}

def manage_user(payload):
    try:
        response = requests.post(MANAGE_URL, json=payload, headers=headers)
        
        if response.status_code == 200:
            print(f"操作成功: {payload['user_id']}")
            print("响应:", response.json())
        elif response.status_code == 403:
            print("权限不足：你当前的账号不是管理员，或 Token 已失效。")
        elif response.status_code == 404:
            print(f"用户不存在: {payload['user_id']}")
        else:
            print(f"错误 {response.status_code}: {response.text}")
    except Exception as e:
        print(f"请求异常: {e}")

# 测试执行
if __name__ == "__main__":
    manage_user(payload_upgrade)