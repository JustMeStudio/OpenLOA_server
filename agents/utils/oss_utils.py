"""
OSS工具模块 - 提供通用的文件上传和转存功能

包含：
- upload_file_to_oss: 将本地文件上传到阿里云OSS
- mirror_to_oss: 从URL下载文件并转存到OSS
"""

import os
import asyncio
import oss2
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

# --- 阿里云 OSS 配置 ---
ALIYUN_ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID")
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET")
ALIYUN_OSS_ENDPOINT = os.getenv("ALIYUN_OSS_ENDPOINT")
ALIYUN_OSS_BUCKET_NAME = os.getenv("ALIYUN_OSS_BUCKET_NAME")

# --- CDN 配置 ---
CDN_ENABLED = os.getenv("CDN_ENABLED", "False").lower() == "true"
CDN_DOMAIN = os.getenv("CDN_DOMAIN", "")
CDN_PROTOCOL = os.getenv("CDN_PROTOCOL", "https")

# 准备线程池，用于跑同步的 OSS 上传
executor = ThreadPoolExecutor(max_workers=10)


def _sync_oss_upload(local_file_path: str, oss_key: str) -> str:
    """
    同步上传函数，供线程池调用
    """
    auth = oss2.Auth(ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, ALIYUN_OSS_ENDPOINT, ALIYUN_OSS_BUCKET_NAME)
    # 执行上传
    with open(local_file_path, 'rb') as fileobj:
        result = bucket.put_object(oss_key, fileobj)
    # 检查状态码 200 表示成功
    if result.status == 200:
        # 根据 CDN 配置决定使用的域名
        if CDN_ENABLED and CDN_DOMAIN:
            return f"{CDN_PROTOCOL}://{CDN_DOMAIN}/{oss_key}"
        else:
            url = f"https://{ALIYUN_OSS_BUCKET_NAME}.{ALIYUN_OSS_ENDPOINT}"
            return f"{url}/{oss_key}"
    else:
        raise Exception(f"OSS 上传失败，状态码: {result.status}")


async def upload_file_to_oss(local_path: str, oss_key: str) -> str:
    """
    异步封装：将同步上传逻辑放入线程池执行，避免阻塞 FastAPI
    支持 CDN 加速：如果启用了 CDN，返回 CDN 域名；否则返回 OSS 原始域名
    
    参数:
    - local_path: 本地文件路径
    - oss_key: OSS目标路径（如 "user/logo_files/{conversation_id}/logo_20240412.png"）
    
    返回: OSS文件的访问URL（支持CDN加速）
    """
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"本地文件不存在: {local_path}")
    loop = asyncio.get_event_loop()
    try:
        # 使用线程池运行同步代码
        file_url = await loop.run_in_executor(
            executor, 
            _sync_oss_upload, 
            local_path, 
            oss_key
        )
        # 等待OSS节点同步完成，避免立即访问时404
        await asyncio.sleep(0.5)
        return file_url
    except Exception as e:
        print(f"OSS Upload Error: {str(e)}")
        raise e


async def mirror_to_oss(url: str, oss_key: str) -> dict:
    """
    从任意URL下载文件，转存到自己的OSS，防止原始URL过期
    
    参数:
    - url: 源文件URL（如生图API的临时链接、第三方服务的临时地址等）
    - oss_key: OSS的目标路径和文件名（如 "user/logo_files/{conversation_id}/logo_20240412_120530.png"）
    
    返回: 
    {
        "result": "success|failure",
        "oss_url": "转存后的OSS链接",
        "message": "成功或失败信息"
    }
    
    用法示例：
    ```python
    # 转存生成的LOGO图片
    mirror_result = await mirror_to_oss(
        url="https://api.example.com/image.png",
        oss_key=f"user/logo_files/{conversation_id}/logo_{timestamp}.png"
    )
    
    if mirror_result.get("result") == "success":
        oss_url = mirror_result.get("oss_url")
        # 使用永久的OSS链接
    ```
    """
    # 延迟导入，避免循环依赖
    from agents.utils.com import read_file_from_url
    
    try:
        # 从URL下载文件
        file_data = await read_file_from_url(url, is_binary=True)
        
        if not isinstance(file_data, bytes):
            return {
                "result": "failure",
                "message": f"下载文件失败：{file_data}"
            }
        
        # 从oss_key提取本地临时文件路径
        # oss_key 格式: "user/task_files/{conversation_id}/logo_20240412_120530.png"
        # 我们将其保存到 temp/{conversation_id}/logo_20240412_120530.png
        file_name = os.path.basename(oss_key)
        temp_dir = os.path.join(os.getcwd(), "temp", os.path.dirname(oss_key).split('/')[-1])
        os.makedirs(temp_dir, exist_ok=True)
        
        save_path = os.path.join(temp_dir, file_name)
        
        # 保存文件到本地
        with open(save_path, "wb") as f:
            f.write(file_data)
        
        # 上传到OSS
        oss_url = await upload_file_to_oss(save_path, oss_key)
        
        return {
            "result": "success",
            "oss_url": oss_url,
            "message": "文件已成功转存到OSS"
        }
        
    except Exception as e:
        return {
            "result": "failure",
            "message": f"转存文件到OSS失败：{str(e)}"
        }
