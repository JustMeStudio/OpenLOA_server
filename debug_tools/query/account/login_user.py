import requests

BASE_URL = "http://127.0.0.1:9000"
LOGIN_URL = f"{BASE_URL}/account/login"

# 登录凭证（管理员账号）
login_data = {
    "account": "fdshiwoa@gmail.com",
    "password": "fd941109FD"
}

try:
    # 1. 执行登录
    response = requests.post(LOGIN_URL, json=login_data)
    
    if response.status_code == 200:
        result = response.json()
        access_token = result.get("access_token")
        print("登录成功！")
        print(result)
        
        # 2. 拿到 Token 后，调用需要权限的接口（例如注册新用户）
        # register_new_user(access_token) 
    else:
        print(f"登录失败: {response.status_code}, {response.text}")

except Exception as e:
    print(f"请求异常: {e}")