"""
Redis 缓存管理器 - 全局连接池和通用操作
支持多worker部署，管理Redis连接生命周期
"""
import os
import asyncio
import redis.asyncio as redis
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# 全局 Redis 连接池（redis 库内部管理连接复用和线程安全）
_redis_client: Optional[redis.Redis] = None


async def _get_redis_client() -> redis.Redis:
    """获取全局 Redis 连接池"""
    global _redis_client
    if _redis_client is None:
        print(f"🔌 创建 Redis 连接池: {REDIS_URL}")
        try:
            _redis_client = redis.from_url(
                REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True
            )
            print(f"✅ Redis 连接池创建成功")
        except Exception as e:
            print(f"❌ Redis 连接池创建失败: {e}")
            raise
    return _redis_client


class RedisManager:
    """Redis缓存管理器（全局连接池，支持多worker部署）"""
    
    @staticmethod
    async def get_connection() -> redis.Redis:
        """获取全局 Redis 连接"""
        return await _get_redis_client()
    
    async def set(self, key: str, value: str, expire: int = None):
        """设置键值对"""
        redis_client = await self.get_connection()
        if expire:
            await redis_client.setex(key, expire, value)
        else:
            await redis_client.set(key, value)
    
    async def get(self, key: str) -> Optional[str]:
        """获取键值"""
        redis_client = await self.get_connection()
        return await redis_client.get(key)
    
    async def delete(self, *keys: str):
        """删除一个或多个键（带强制超时）"""
        if not keys:
            return
        
        try:
            
            print(f"    📍 delete 开始获取连接，keys={keys}")
            redis_client = await asyncio.wait_for(
                self.get_connection(),
                timeout=2.0
            )
            print(f"    📍 delete 连接获取成功，准备执行 delete 操作")
            
            # 为 delete 操作设置 3 秒超时，防止卡死
            print(f"    📍 执行 delete 命令...")
            result = await asyncio.wait_for(
                redis_client.delete(*keys),
                timeout=3.0
            )
            print(f"    📍 delete 操作完成，删除了 {result} 个 key")
            
        except asyncio.TimeoutError as e:
            print(f"    ⏱️ Redis delete 超时（可能是多worker竞争或Redis无响应）: keys={keys}")
        except Exception as e:
            print(f"    ❌ Redis delete 异常: {type(e).__name__}: {str(e)[:100]}")
    
    async def hset(self, key: str, field: str, value: str):
        """设置哈希字段"""
        redis_client = await self.get_connection()
        await redis_client.hset(key, field, value)
    
    async def hget(self, key: str, field: str) -> Optional[str]:
        """获取哈希字段"""
        redis_client = await self.get_connection()
        return await redis_client.hget(key, field)
    
    async def hgetall(self, key: str) -> dict:
        """获取哈希所有字段"""
        redis_client = await self.get_connection()
        return await redis_client.hgetall(key)
    
    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        redis_client = await self.get_connection()
        result = await redis_client.exists(key)
        return bool(result)
    
    async def expire(self, key: str, seconds: int) -> bool:
        """设置键的过期时间"""
        redis_client = await self.get_connection()
        return await redis_client.expire(key, seconds)
    
    @staticmethod
    async def disconnect():
        """关闭 Redis 连接池"""
        global _redis_client
        if _redis_client is not None:
            await _redis_client.close()
            _redis_client = None
