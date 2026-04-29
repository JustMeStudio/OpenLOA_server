"""
用户级限流管理器 - 防止单个用户异常使用
使用Redis实现，维度为user_id，支持多worker部署
"""
import time
from typing import Tuple, Optional
from dotenv import load_dotenv
from utils.redis_manager import RedisManager

load_dotenv()

class UserRateLimiter:
    """用户级限流器"""
    
    def __init__(self):
        self.redis_manager = RedisManager()
    
    async def _get_window_key(self, user_id: str, endpoint: str, window_seconds: int) -> str:
        """
        生成Redis key - 基于当前时间窗口
        
        例如：window=60秒，则key包含分钟级时间戳
        key = "rate_limit:user:{user_id}:{endpoint}:{current_window}"
        """
        current_window = int(time.time()) // window_seconds
        return f"rate_limit:user:{user_id}:{endpoint}:{current_window}"
    
    async def check_rate_limit(
        self, 
        user_id: str, 
        endpoint: str, 
        limit: int, 
        window_seconds: int
    ) -> Tuple[bool, int, int]:
        """
        检查用户是否超过限流
        
        返回值：(allow: bool, current_count: int, remaining: int)
        - allow=True 表示允许访问
        - allow=False 表示超过限流
        - current_count 当前窗口内的请求数
        - remaining 剩余可用请求数
        
        示例：
        allow, count, remaining = await limiter.check_rate_limit(
            "user_123", "/agent/chat", 3, 60
        )
        if not allow:
            return {"status": 429, "remaining_seconds": window_seconds}
        """
        try:
            redis_key = await self._get_window_key(user_id, endpoint, window_seconds)
            redis_client = await self.redis_manager.get_connection()
            
            # 原子操作：执行INCR并获取当前计数
            current_count = await redis_client.incr(redis_key)
            
            # 如果是首次请求（current_count==1），设置过期时间
            if current_count == 1:
                await redis_client.expire(redis_key, window_seconds)
            
            # 检查是否超限
            allow = current_count <= limit
            remaining = limit - current_count
            
            # 日志记录 - 与 IP 限流格式保持一致
            status_icon = "✅" if allow else "🚫"
            print(f"{status_icon} [User:{user_id}] {endpoint}: {current_count}/{limit} (remaining: {remaining})")
            
            return allow, current_count, remaining
            
        except Exception as e:
            import traceback
            error_msg = f"{type(e).__name__}: {str(e)}"
            print(f"❌ [User:{user_id}] {endpoint} - Redis 异常 - {error_msg}")
            print(f"   堆栈跟踪:\n{traceback.format_exc()}")
            # 出错时允许通过（fail-open策略）
            print(f"⚠️  由于 Redis 异常，本次请求将被允许通过（fail-open）")
            return True, 0, limit
    
    async def get_rate_limit_status(
        self, 
        user_id: str, 
        endpoint: str, 
        window_seconds: int
    ) -> dict:
        """获取某个用户的当前限流状态（用于调试）"""
        try:
            redis_key = await self._get_window_key(user_id, endpoint, window_seconds)
            redis_client = await self.redis_manager.get_connection()
            count = await redis_client.get(redis_key)
            
            return {
                "user_id": user_id,
                "endpoint": endpoint,
                "current_count": int(count) if count else 0,
                "window_seconds": window_seconds,
                "redis_key": redis_key
            }
        except Exception as e:
            print(f"❌ 获取用户限流状态异常: {e}")
            return {}


# 全局限流器实例
_user_limiter: Optional[UserRateLimiter] = None

async def get_user_limiter() -> UserRateLimiter:
    """获取全局用户级限流器实例"""
    global _user_limiter
    if _user_limiter is None:
        _user_limiter = UserRateLimiter()
    return _user_limiter
