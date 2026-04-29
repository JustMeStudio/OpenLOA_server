"""
生成任务管理模块 - 支持多worker部署
通过Redis实现跨进程数据共享，支持按agent分别限流
管理：限流、停止标志
"""
import asyncio
import time
from typing import Dict, Optional
from dotenv import load_dotenv
from utils.config_loader import get_agent_config, get_all_config
from utils.redis_manager import RedisManager

load_dotenv()

# ===== 本地内存：单worker内线程级别的限流 =====
# （多worker部署时，全局限流 = 单worker限流 × worker数）
_semaphores: Dict[str, Optional[asyncio.Semaphore]] = {}
_active_jobs: Dict[str, int] = {}  # {agent_name: active_count}
_semaphores_lock = asyncio.Lock()


async def rebuild_semaphores():
    """根据配置重建信号量（支持热更新）"""
    global _semaphores, _active_jobs
    
    async with _semaphores_lock:
        config = await get_all_config()
        agents = config.get("agents", {})
        
        # 清空旧的信号量
        _semaphores = {}
        _active_jobs = {}
        
        # 重建所有agent的信号量
        for agent_name, agent_cfg in agents.items():
            max_concurrent = agent_cfg.get("max_concurrent")
            enabled = agent_cfg.get("enabled", False)
            
            if enabled and max_concurrent:
                _semaphores[agent_name] = asyncio.Semaphore(max_concurrent)
                _active_jobs[agent_name] = 0
                print(f"✅ {agent_name}: 限流器已创建，最大并发 = {max_concurrent}")
            else:
                _semaphores[agent_name] = None
                _active_jobs[agent_name] = 0
                print(f"⏭️  {agent_name}: 不限流")


async def acquire_slot(conversation_id: str, agent_name: str) -> bool:
    """
    获取限流槽位（本地Semaphore + 数据库计数）
    
    ⚠️ 多worker情况下：全局限流 = 单worker限流 × worker数
    如需全局限流准确，需在agent配置中相应调整max_concurrent值
    
    返回值：True = 成功获得，False = agent不存在或未启用
    """
    async with _semaphores_lock:
        semaphore = _semaphores.get(agent_name)
    
    if semaphore is None:
        return True  # 不限流
    
    # 显示等待信息
    current_active = _active_jobs.get(agent_name, 0)
    config = await get_agent_config(agent_name)
    max_con = config.get("max_concurrent") if config else None
    print(f"⏳ [{agent_name}:{conversation_id}] 等待投递槽位... (当前: {current_active}/{max_con})")
    
    # 阻塞等待直到获得槽位
    await semaphore.acquire()
    
    # 更新本地计数
    _active_jobs[agent_name] = _active_jobs.get(agent_name, 0) + 1
    print(f"✅ [{agent_name}:{conversation_id}] 获得投递槽位 (活跃: {_active_jobs[agent_name]})")
    
    return True


async def release_slot(conversation_id: str, agent_name: str):
    """释放限流槽位"""
    async with _semaphores_lock:
        semaphore = _semaphores.get(agent_name)
    
    if semaphore is None:
        return
    
    semaphore.release()
    _active_jobs[agent_name] = max(0, _active_jobs.get(agent_name, 1) - 1)
    print(f"✅ [{agent_name}:{conversation_id}] 释放投递槽位 (活跃: {_active_jobs[agent_name]})")


async def get_semaphore_status() -> Dict[str, Dict]:
    """获取所有agent的信号量状态"""
    status = {}
    for agent_name in _semaphores.keys():
        semaphore = _semaphores[agent_name]
        active = _active_jobs.get(agent_name, 0)
        
        if semaphore is None:
            max_con = "无限制"
            utilization = "N/A"
        else:
            max_con = semaphore._value + active
            utilization = f"{active}/{max_con}"
        
        status[agent_name] = {
            "active": active,
            "max_concurrent": max_con,
            "utilization": utilization
        }
    
    return status


# ===== 停止标志管理（数据库 + 内存缓存混合） =====

async def set_stop_flag(conversation_id: str, value: bool = True):
    """
    设置停止标志（写入Redis，确保跨worker可见）
    
    参数：
    - conversation_id: 对话ID
    - value: True=停止，False=继续
    
    TTL: 3 秒（用户停止后3秒内一般不会发起新消息）
    """
    flag_val = "1" if value else "0"
    
    try:
        cache_manager = RedisManager()
        await cache_manager.set(f"stop_flags:{conversation_id}", flag_val, expire=3)
        print(f"🛑 停止标志已{'设置' if value else '清除'}: {conversation_id}")
    except Exception as e:
        print(f"⚠️ 设置停止标志失败: {e}")


async def get_stop_flag(conversation_id: str) -> bool:
    """
    获取停止标志（从Redis读取，支持跨worker）
    """
    try:
        cache_manager = RedisManager()
        value = await cache_manager.get(f"stop_flags:{conversation_id}")
        if value:
            return bool(int(value))
    except Exception as e:
        print(f"⚠️ 读取停止标志失败: {e}")
    
    return False





async def is_stopped(conversation_id: str) -> bool:
    """检查是否已停止"""
    return await get_stop_flag(conversation_id)
