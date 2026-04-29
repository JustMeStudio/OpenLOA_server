import os
import uuid
import oss2
import time
import aiosqlite
import json
import base64
import hmac
import hashlib
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from .security import get_current_user, get_current_admin

router = APIRouter()


load_dotenv()

DB_PATH = os.getenv("DB_PATH")

# --- 阿里云 OSS 配置 ---
ALIYUN_ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID")
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET")
ALIYUN_OSS_ENDPOINT = os.getenv("ALIYUN_OSS_ENDPOINT")
ALIYUN_OSS_BUCKET_NAME = os.getenv("ALIYUN_OSS_BUCKET_NAME")
UPLOAD_MAX_FILE_SIZE = int(os.getenv("UPLOAD_MAX_FILE_SIZE", 50 ))* 1024 * 1024  # 默认 50MB

# --- CDN 配置 ---
CDN_ENABLED = os.getenv("CDN_ENABLED", "False").lower() == "true"
CDN_DOMAIN = os.getenv("CDN_DOMAIN", "")
CDN_PROTOCOL = os.getenv("CDN_PROTOCOL", "https")

# 最终展示用的基础域名
BASE_URL = f"https://{ALIYUN_OSS_BUCKET_NAME}.{ALIYUN_OSS_ENDPOINT}"

#--------------工具函数----------------
async def update_user_avatar(user_id: str, avatar_url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_info SET avatar_url = ? WHERE user_id = ?",
            (avatar_url, user_id)
        )
        await db.commit()
    
#----------接口-----------

class get_presigned_url_class(BaseModel):
    file_name: str
    mode: str = "user_uploads"  # 可选值：user_uploads, task_files 等，根据不同用途分类存储
    content_type: str = "application/octet-stream"
    file_size: int  # 前端告知文件大小，后端验证

@router.post("/get_presigned_url")
async def get_presigned_url(
    request: get_presigned_url_class,
    current_user_id: str = Depends(get_current_user),
):
    # 1. 前端校验
    if request.file_size > UPLOAD_MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400, 
            detail=f"文件大小超过限制。最大 {UPLOAD_MAX_FILE_SIZE / 1024 / 1024}MB，你的文件为 {round(request.file_size / 1024 / 1024, 2)}MB"
        )
    
    # 2. 构建唯一的存储路径
    base_name, ext = os.path.splitext(request.file_name)
    timestamp = int(time.time())
    safe_file_name = f"{base_name}_{timestamp}{ext.lower()}"
    object_name = f"user/{request.mode}/{current_user_id}/{safe_file_name}"
    
    try:
        # 3. 生成 POST Policy（最安全的方式，OSS 服务端会验证文件大小）
        expiration = datetime.utcnow() + timedelta(hours=1)
        expiration_str = expiration.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        # Policy 对象，包含 content-length-range 条件
        policy_dict = {
            "expiration": expiration_str,
            "conditions": [
                {"bucket": ALIYUN_OSS_BUCKET_NAME},
                {"key": object_name},
                ["content-length-range", 0, UPLOAD_MAX_FILE_SIZE],  # OSS 服务端真实限制
                {"Content-Type": request.content_type}
            ]
        }
        
        # 对 policy 进行 base64 编码
        policy_json = json.dumps(policy_dict)
        policy_base64 = base64.b64encode(policy_json.encode('utf-8')).decode('utf-8')
        
        # 对 policy_base64 进行 HMAC-SHA1 签名
        signature = base64.b64encode(
            hmac.new(
                ALIYUN_ACCESS_KEY_SECRET.encode('utf-8'),
                policy_base64.encode('utf-8'),
                hashlib.sha1
            ).digest()
        ).decode('utf-8')
        
        # 4. 返回 POST 表单所需的所有参数
        # 根据 CDN 配置决定使用的域名
        if CDN_ENABLED and CDN_DOMAIN:
            static_url = f"{CDN_PROTOCOL}://{CDN_DOMAIN}/{object_name}"
        else:
            static_url = f"{BASE_URL}/{object_name}"
        
        permanent_url = f"{BASE_URL}/{object_name}"
        print(f"Generated pre-signed URL for file {request.file_name}: {permanent_url}")
        return {
            "status": "success",
            "data": {
                "upload_url": f"https://{ALIYUN_OSS_BUCKET_NAME}.{ALIYUN_OSS_ENDPOINT.replace('https://', '')}",  # 表单 action 地址
                "oss_key": object_name,
                "access_key_id": ALIYUN_ACCESS_KEY_ID,
                "policy": policy_base64,
                "signature": signature,
                "static_url": static_url,
                "max_file_size": UPLOAD_MAX_FILE_SIZE
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))