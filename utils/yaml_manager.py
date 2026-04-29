"""
YAML 配置文件管理工具
"""
import os
import yaml
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional


class YAMLManager:
    """对YAML文件的读写操作进行封装，确保线程安全"""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        # 使用文件锁确保并发安全
        self.lock = asyncio.Lock()
    
    async def read(self) -> Dict[str, Any]:
        """异步读取YAML文件"""
        async with self.lock:
            if not os.path.exists(self.file_path):
                return {}
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    return data if data else {}
            except Exception as e:
                raise Exception(f"读取YAML文件失败: {str(e)}")
    
    async def write(self, data: Dict[str, Any]) -> None:
        """异步写入YAML文件"""
        async with self.lock:
            try:
                # 创建备份（可选但推荐）
                backup_path = f"{self.file_path}.backup"
                if os.path.exists(self.file_path):
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        backup_data = f.read()
                    with open(backup_path, "w", encoding="utf-8") as f:
                        f.write(backup_data)
                
                # 写入新数据
                with open(self.file_path, "w", encoding="utf-8") as f:
                    yaml.dump(
                        data,
                        f,
                        default_flow_style=False,
                        allow_unicode=True,
                        sort_keys=False
                    )
            except Exception as e:
                raise Exception(f"写入YAML文件失败: {str(e)}")
    
    async def update_agent(self, agent_name: str, agent_data: Dict[str, Any]) -> None:
        """更新特定agent的配置"""
        data = await self.read()
        data[agent_name] = agent_data
        await self.write(data)
    
    async def get_agent(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """获取特定agent的配置"""
        data = await self.read()
        return data.get(agent_name)


# 全局实例（懒加载）
_profiles_manager: Optional[YAMLManager] = None

async def get_profiles_manager() -> YAMLManager:
    """获取全局的profiles管理器实例"""
    global _profiles_manager
    if _profiles_manager is None:
        profiles_path = os.path.join(
            os.path.dirname(__file__), 
            "..", 
            "configs", 
            "profiles.yaml"
        )
        _profiles_manager = YAMLManager(profiles_path)
    return _profiles_manager
