"""
配置加载模块
应用启动时加载rate_limit.yaml配置
"""
import yaml
import aiofiles
from typing import Dict, Any, Optional
from pathlib import Path

# 全局配置（启动时加载，之后只读）
_config: Dict[str, Any] = {}
_config_file_path = Path(__file__).parent.parent / "configs" / "rate_limit.yaml"

# IP限流配置
_ip_rate_limit_config: Dict[str, Any] = {}
_ip_rate_limit_config_file_path = Path(__file__).parent.parent / "configs" / "ip_rate_limit.yaml"

# 用户级限流配置
_user_rate_limit_config: Dict[str, Any] = {}
_user_rate_limit_config_file_path = Path(__file__).parent.parent / "configs" / "user_rate_limit.yaml"


async def load_config_sync() -> Dict[str, Any]:
    """异步加载YAML配置文件"""
    try:
        async with aiofiles.open(_config_file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            config = yaml.safe_load(content)
            if not config:
                config = {"agents": {}}
            return config
    except FileNotFoundError:
        print(f"⚠️ 配置文件不存在: {_config_file_path}")
        return {"agents": {}}
    except yaml.YAMLError as e:
        print(f"❌ YAML解析错误: {e}")
        return {"agents": {}}


async def init_config():
    """应用启动时初始化配置"""
    global _config, _ip_rate_limit_config, _user_rate_limit_config
    _config = await load_config_sync()
    _ip_rate_limit_config = await load_ip_rate_limit_config_sync()
    _user_rate_limit_config = await load_user_rate_limit_config_sync()
    print(f"✅ 限流配置加载完成，共{len(_config.get('agents', {}))}个agent")
    print_config()
    print_ip_rate_limit_config()
    print_user_rate_limit_config()


async def load_ip_rate_limit_config_sync() -> Dict[str, Any]:
    """异步加载IP限流配置文件"""
    try:
        async with aiofiles.open(_ip_rate_limit_config_file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            config = yaml.safe_load(content)
            if not config:
                config = {"ip_rate_limits": {}, "enabled": False}
            return config
    except FileNotFoundError:
        print(f"⚠️ IP限流配置文件不存在: {_ip_rate_limit_config_file_path}")
        return {"ip_rate_limits": {}, "enabled": False}
    except yaml.YAMLError as e:
        print(f"❌ YAML解析错误: {e}")
        return {"ip_rate_limits": {}, "enabled": False}


async def load_user_rate_limit_config_sync() -> Dict[str, Any]:
    """异步加载用户级限流配置文件"""
    try:
        async with aiofiles.open(_user_rate_limit_config_file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            config = yaml.safe_load(content)
            if not config:
                config = {"user_rate_limits": {}, "enabled": False}
            return config
    except FileNotFoundError:
        print(f"⚠️ 用户级限流配置文件不存在: {_user_rate_limit_config_file_path}")
        return {"user_rate_limits": {}, "enabled": False}
    except yaml.YAMLError as e:
        print(f"❌ YAML解析错误: {e}")
        return {"user_rate_limits": {}, "enabled": False}


async def get_agent_config(agent_name: str) -> Optional[Dict]:
    """获取特定agent的配置"""
    return _config.get("agents", {}).get(agent_name)


async def get_all_config() -> Dict[str, Any]:
    """获取所有配置"""
    return _config.copy()


def print_config():
    """打印当前配置"""
    print("\n" + "="*60)
    print("📋 当前限流配置:")
    print("="*60)
    for agent_name, config in _config.get("agents", {}).items():
        enabled = "✅启用" if config.get("enabled") else "⏭️ 禁用"
        max_concurrent = config.get("max_concurrent")
        limit_desc = f"最大并发: {max_concurrent}" if max_concurrent else "无限制"
        print(f"  [{enabled}] {agent_name:15} | {limit_desc}")
    print("="*60 + "\n")


def print_ip_rate_limit_config():
    """打印IP限流配置"""
    print("\n" + "="*60)
    print("📋 IP级限流配置:")
    print("="*60)
    if not _ip_rate_limit_config.get("enabled"):
        print("  ⏭️  IP限流已禁用")
    else:
        for rule_name, rule_config in _ip_rate_limit_config.get("ip_rate_limits", {}).items():
            if not rule_config.get("enabled", True):
                continue
            endpoints = rule_config.get("endpoints", [])
            limit = rule_config.get("limit", "?")
            window = rule_config.get("window_seconds", "?")
            print(f"  ✅ [{rule_name}] {limit}次/{window}秒")
            for ep in endpoints:
                print(f"      └─ {ep}")
    print("="*60 + "\n")


def print_user_rate_limit_config():
    """打印用户级限流配置"""
    print("\n" + "="*60)
    print("📋 用户级限流配置:")
    print("="*60)
    if not _user_rate_limit_config.get("enabled"):
        print("  ⏭️  用户级限流已禁用")
    else:
        for rule_name, rule_config in _user_rate_limit_config.get("user_rate_limits", {}).items():
            if not rule_config.get("enabled", True):
                continue
            endpoints = rule_config.get("endpoints", [])
            limit = rule_config.get("limit", "?")
            window = rule_config.get("window_seconds", "?")
            print(f"  ✅ [{rule_name}] {limit}次/{window}秒")
            for ep in endpoints:
                print(f"      └─ {ep}")
    print("="*60 + "\n")


async def validate_config() -> bool:
    """验证配置有效性"""
    config = await get_all_config()
    agents = config.get("agents", {})
    
    if not agents:
        print("⚠️ 警告：未找到任何agent配置")
        return False
    
    for agent_name, cfg in agents.items():
        max_con = cfg.get("max_concurrent")
        # max_concurrent 应该是整数或null
        if max_con is not None and (not isinstance(max_con, int) or max_con <= 0):
            print(f"❌ 配置错误：{agent_name} 的 max_concurrent 应为正整数或null")
            return False
    
    return True


async def get_ip_rate_limit_config() -> Dict[str, Any]:
    """获取IP限流配置"""
    return _ip_rate_limit_config.copy()


async def get_rate_limit_rule(endpoint: str) -> Optional[Dict[str, Any]]:
    """
    根据端点获取对应的IP限流规则
    
    返回格式: {limit: int, window_seconds: int}
    如果未找到匹配规则，返回default规则
    """
    ip_config = await get_ip_rate_limit_config()
    
    if not ip_config.get("enabled"):
        return None
    
    # 遍历所有规则，找到匹配的endpoint
    for rule_name, rule_config in ip_config.get("ip_rate_limits", {}).items():
        if rule_name == "default":
            continue
        
        if not rule_config.get("enabled", True):
            continue
        
        endpoints = rule_config.get("endpoints", [])
        if endpoint in endpoints:
            return {
                "limit": rule_config.get("limit"),
                "window_seconds": rule_config.get("window_seconds"),
                "rule_name": rule_name
            }
    
    # 如果没有匹配，使用default规则
    default_rule = ip_config.get("ip_rate_limits", {}).get("default", {})
    if default_rule.get("enabled", True):
        return {
            "limit": default_rule.get("limit"),
            "window_seconds": default_rule.get("window_seconds"),
            "rule_name": "default"
        }
    
    return None


async def get_user_rate_limit_config() -> Dict[str, Any]:
    """获取用户级限流配置"""
    return _user_rate_limit_config.copy()


async def get_user_rate_limit_rule(endpoint: str) -> Optional[Dict[str, Any]]:
    """
    根据端点获取对应的用户级限流规则
    
    返回格式: {limit: int, window_seconds: int, rule_name: str}
    如果未找到匹配规则，返回default规则
    如果用户级限流未启用，返回None
    """
    user_config = await get_user_rate_limit_config()
    
    if not user_config.get("enabled"):
        return None
    
    # 遍历所有规则，找到匹配的endpoint
    for rule_name, rule_config in user_config.get("user_rate_limits", {}).items():
        if rule_name == "default":
            continue
        
        if not rule_config.get("enabled", True):
            continue
        
        endpoints = rule_config.get("endpoints", [])
        if endpoint in endpoints:
            return {
                "limit": rule_config.get("limit"),
                "window_seconds": rule_config.get("window_seconds"),
                "rule_name": rule_name
            }
    
    # 如果没有匹配，使用default规则
    default_rule = user_config.get("user_rate_limits", {}).get("default", {})
    if default_rule.get("enabled", True):
        return {
            "limit": default_rule.get("limit"),
            "window_seconds": default_rule.get("window_seconds"),
            "rule_name": "default"
        }
    
    return None
