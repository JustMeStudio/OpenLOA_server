import requests

# 配置信息
BASE_URL = "http://127.0.0.1:8080"  # 请根据你的实际端口修改
REGISTER_URL = f"{BASE_URL}/account/register"
ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoiM2Y0Y2I0NDQtYWU0OS00YjhkLTg4NDEtNGZmYmFjNGQyYWY2IiwiZXhwIjoxNzcwMjE5ODkwfQ.P7S35bURDXF4dIMIAWTYIxd9jYedYpcpwgrnMrTxMFQ"  # 必须提供管理员权限的 Token

# 注册数据
payload = {
    "email": "fdshiwoa@163.com",
    "password": "fd941109FD",
    "confirm_password": "fd941109FD",
    "user_role": "admin"
}

# 设置 Header
headers = {
    "Authorization": f"Bearer {ADMIN_TOKEN}",
    "Content-Type": "application/json"
}

try:
    response = requests.post(REGISTER_URL, json=payload, headers=headers)
    
    print(f"Status Code: {response.status_code}")
    try:
        print("Response JSON:", response.json())
    except:
        print("Response Text (Raw):", response.text) # 打印出原始报错信息

except Exception as e:
    print(f"Request error: {e}")