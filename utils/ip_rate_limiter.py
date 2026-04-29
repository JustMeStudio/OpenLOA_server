"""
IP级限流管理器 - 防止恶意调用和暴力破解
使用Redis实现，支持多worker部署
"""
import time
from datetime import datetime
from typing import Tuple, Optional
from dotenv import load_dotenv
from utils.redis_manager import RedisManager

load_dotenv()

class IPRateLimiter:
    """IP级限流器"""
    
    def __init__(self):
        self.redis_manager = RedisManager()
    
    async def _get_window_key(self, ip: str, endpoint: str, window_seconds: int) -> str:
        """
        生成Redis key - 基于当前时间窗口
        确保同一分钟内的请求都用同一个key
        
        例如：window=60秒，则key包含分钟级时间戳
        """
        current_window = int(time.time()) // window_seconds
        return f"rate_limit:ip:{ip}:{endpoint}:{current_window}"
    
    async def check_rate_limit(
        self, 
        ip: str, 
        endpoint: str, 
        limit: int, 
        window_seconds: int
    ) -> Tuple[bool, int, int]:
        """
        检查IP是否超过限流
        
        返回值：(allow: bool, current_count: int, remaining: int)
        - allow=True 表示允许访问
        - allow=False 表示超过限流
        - current_count 当前窗口内的请求数
        - remaining 剩余可用请求数（可能为负）
        
        示例：
        allow, count, remaining = await limiter.check_rate_limit("192.168.1.1", "/account/login", 5, 60)
        if not allow:
            return {"status": 429, "retry_after": remaining}
        """
        try:
            redis_key = await self._get_window_key(ip, endpoint, window_seconds)
            redis_client = await self.redis_manager.get_connection()
            
            # 原子操作：获取当前计数并+1
            current_count = await redis_client.incr(redis_key)
            
            # 如果是首次请求（current_count==1），设置过期时间
            if current_count == 1:
                await redis_client.expire(redis_key, window_seconds)
            
            # 检查是否超限
            allow = current_count <= limit
            remaining = limit - current_count
            
            # 日志记录
            status_icon = "✅" if allow else "🚫"
            print(f"{status_icon} [{ip}] {endpoint}: {current_count}/{limit} (remaining: {remaining})")
            
            return allow, current_count, remaining
            
        except Exception as e:
            print(f"⚠️ IP限流检查异常: {type(e).__name__}: {str(e)[:100]}")
            # 出错时允许通过（fail-open策略，避免Redis故障导致服务完全不可用）
            return True, 0, limit
    
    async def get_rate_limit_status(self, ip: str, endpoint: str, window_seconds: int) -> dict:
        """获取某个IP的当前限流状态（用于调试）"""
        try:
            redis_key = await self._get_window_key(ip, endpoint, window_seconds)
            count = await self.redis_manager.get(redis_key)
            
            return {
                "ip": ip,
                "endpoint": endpoint,
                "current_count": int(count) if count else 0,
                "window_seconds": window_seconds,
                "redis_key": redis_key
            }
        except Exception as e:
            print(f"❌ 获取限流状态异常: {e}")
            return {}


# 全局限流器实例
_ip_limiter: Optional[IPRateLimiter] = None

async def get_ip_limiter() -> IPRateLimiter:
    """获取全局IP限流器实例"""
    global _ip_limiter
    if _ip_limiter is None:
        _ip_limiter = IPRateLimiter()
    return _ip_limiter
