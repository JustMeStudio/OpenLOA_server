import requests

# 配置
BASE_URL = "http://127.0.0.1:8000"
UPDATE_PWD_URL = f"{BASE_URL}/account/update_password"
ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoiM2Y0Y2I0NDQtYWU0OS00YjhkLTg4NDEtNGZmYmFjNGQyYWY2IiwiZXhwIjoxNzcwMjIwODkzfQ.65e9TrLbzOWUXCCiQKzYU9ETz02YMbnJgmHfK_IqrHQ"


# 修改密码的数据负载
payload = {
    "login_name": "liuyalan",
    "password": "liuyalan1234",       # 必须是数据库中当前的正确密码
    "new_password": "liuyalan5678",  # 符合复杂度要求的新密码
    "confirm_new_password": "liuyalan5678"
}

# 设置 Header
headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

try:
    response = requests.post(UPDATE_PWD_URL, json=payload, headers=headers)
    
    if response.status_code == 200:
        print("密码修改成功！请使用新密码重新登录。")
        print("响应结果:", response.json())
    elif response.status_code == 400:
        print(response.json().get("detail"))
    else:
        print(f"服务器错误: {response.status_code}")
        print("错误详情:", response.text)

except Exception as e:
    print(f"网络请求发生异常: {e}")