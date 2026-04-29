import os
import aiosqlite
import jwt
import traceback
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from typing import Annotated
from utils.user_rate_limiter import get_user_limiter
from utils.config_loader import get_user_rate_limit_rule

router = APIRouter()


load_dotenv()

DB_PATH = os.getenv("DB_PATH")
SECRET_KEY = os.getenv("SECRET_KEY")
ACCESS_TOKEN_EXPIRE_HOURS = float(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS"))


# 在文件顶部，router 定义之后添加
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/account/user_login")  # tokenUrl 只是给 Swagger 显示用的，实际不影响

async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]):
    """
    校验 access_token (JWT)，并验证对应的 refresh_token 是否还在有效期内
    返回 user_id（或整个 user 对象）
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # 解码 JWT access_token
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user_id: str = payload.get("user_id")
        
        if user_id is None:
            raise credentials_exception
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Access token has expired")
    except jwt.InvalidTokenError:
        raise credentials_exception

    async with aiosqlite.connect(DB_PATH) as conn:
        try:
            # 查询 user_token 表，验证 refresh_token 是否存在且未过期
            # 注意：我们不从 access_token 里取 refresh_token（不安全），而是验证这个 user_id 是否还有有效的 refresh_token
            cursor = await conn.execute("""
                SELECT refresh_token, expire_time 
                FROM user_token 
                WHERE user_id = ? AND expire_time > ?
            """, (user_id, datetime.now().isoformat()))

            token_record = await cursor.fetchone()
            if not token_record:
                raise credentials_exception

            # 可以额外检查 refresh_token 是否匹配（如果前端同时传了 refresh_token，可加强校验）
            # 但大多数场景只校验 access_token + 数据库中该用户有有效 refresh_token 即可

            # 可选：顺便查一下用户是否启用
            cursor = await conn.execute("SELECT is_enabled FROM user_info WHERE user_id = ?", (user_id,))
            user = await cursor.fetchone()
            if not user or user[0] != 1:
                raise HTTPException(status_code=403, detail="User is disabled")

            return user_id  # 或返回 {"user_id": user_id, ...} 更多信息

        except HTTPException:
            raise
        except Exception as e:
            raise credentials_exception


# 如果需要 admin-only 接口，可以进一步封装一个依赖
async def get_current_admin(current_user_id: Annotated[str, Depends(get_current_user)]):
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute("SELECT user_role FROM user_info WHERE user_id = ?", (current_user_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=403, detail="Admin access required")
        role = row[0]
        if role != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
    return current_user_id


# ===== 用户级限流依赖（可选，用于特定接口的额外限流控制） =====
async def check_user_rate_limit(
    current_user_id: Annotated[str, Depends(get_current_user)],
    request: Request
):
    """
    用户级限流依赖 - 可在特定接口中使用
    
    示例用法：
    @router.post("/agent/chat")
    async def chat_with_agent(
        request: chat_with_agent_class,
        _: Annotated[None, Depends(check_user_rate_limit)]  # 添加这行
    ):
        ...
    """
    endpoint = request.url.path
    rule = await get_user_rate_limit_rule(endpoint)
    
    if rule is None:
        # 该端点未配置用户级限流，允许通过
        return None
    
    limiter = await get_user_limiter()
    limit = rule["limit"]
    window = rule["window_seconds"]
    
    allow, current_count, remaining = await limiter.check_rate_limit(
        current_user_id,
        endpoint,
        limit,
        window
    )
    
    if not allow:
        raise HTTPException(
            status_code=429,
            detail=f"User rate limit exceeded. Maximum {limit} requests per {window}s.",
            headers={
                "Retry-After": str(window),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(max(0, remaining)),
            }
        )
    
    return None


class refresh_token_class(BaseModel):
    refresh_token: str

@router.post("/refresh_token")
async def refresh_token_endpoint(request: refresh_token_class):
    """
    使用长效 refresh_token 换取新的短效 access_token
    """
    token_value = request.refresh_token

    async with aiosqlite.connect(DB_PATH) as conn:
        try:
            # 1. 在数据库中查询该 refresh_token 是否存在且未过期
            cursor = await conn.execute("""
                SELECT user_id, expire_time
                FROM user_token
                WHERE refresh_token = ?
            """, (token_value,))
            
            record = await cursor.fetchone()
            
            if not record:
                raise HTTPException(status_code=401, detail="Invalid refresh token")
            
            user_id, expire_time_str = record
            
            # 2. 检查是否过期
            # 假设 expire_time 存储的是 ISO 格式字符串
            expire_time = datetime.fromisoformat(expire_time_str)
            if expire_time < datetime.now(timezone.utc):
                raise HTTPException(status_code=401, detail="Refresh token expired, please login again")

            # 3. 检查用户是否仍处于启用状态
            cursor = await conn.execute("SELECT is_enabled FROM user_info WHERE user_id = ?", (user_id,))
            user_status = await cursor.fetchone()
            if not user_status or user_status[0] != 1:
                raise HTTPException(status_code=403, detail="User account is disabled")

            # 4. 生成新的 access_token
            new_access_token = jwt.encode({
                'user_id': user_id,
                'exp': datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
            }, SECRET_KEY, algorithm='HS256')

            return {
                "access_token": new_access_token,
                "token_type": "bearer",
            }

        except HTTPException as e:
            raise e
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Refresh failed: {str(e)}")