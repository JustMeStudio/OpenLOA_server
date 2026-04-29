import requests

# 配置
BASE_URL = "http://127.0.0.1:8000"
UPDATE_INFO_URL = f"{BASE_URL}/account/update_user_info"
# 这里填入你通过 /user_login 接口获取到的 access_token
USER_ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoiM2Y0Y2I0NDQtYWU0OS00YjhkLTg4NDEtNGZmYmFjNGQyYWY2IiwiZXhwIjoxNzcwMjIxOTE5fQ.QEqanYB51LIM20d1P_VpNNI6eQhqZdutUo90pYUJ11I"

# 设置请求头
headers = {
    "Authorization": f"Bearer {USER_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# 准备要修改的数据（login_name 必须与 Token 对应的用户一致）
payload = {
    "login_name": "fanding",
    "user_name": "dean",
    "phone_number": "18810703037",
    "gender": "male"
}

try:
    response = requests.post(UPDATE_INFO_URL, json=payload, headers=headers)
    
    if response.status_code == 200:
        print("个人信息修改成功！")
        print(response.json())
    elif response.status_code == 403:
        print("权限错误：可能是 Token 已过期，或者你尝试修改他人的信息。")
        print(response.json().get("detail"))
    else:
        print(f"请求失败，状态码: {response.status_code}")
        print("错误详情:", response.text)

except Exception as e:
    print(f"发生异常: {e}")