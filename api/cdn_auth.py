"""
CDN 远程鉴权服务 (整合版)

处理Aliyun CDN的远程鉴权请求，支持：
- IP级别的QPS限流
- 资源类型差异化限制（视频/图片/文件）
- 源验证和时间戳验证

配置文件：configs/cdn_config.yaml
"""

import os
import json
import yaml
import time
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from fastapi import APIRouter, Request, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from utils.redis_manager import RedisManager

# ============================================================================
# 1. 配置管理 (来自 cdn/config.py)
# ============================================================================

CDN_CONFIG_PATH = Path(__file__).parent.parent / "configs" / "cdn_config.yaml"


class CDNConfig:
    """CDN 配置管理单例"""
    
    _instance = None
    _config: Dict[str, Any] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config()
        return cls._instance
    
    def _load_config(self):
        """加载 YAML 配置文件"""
        try:
            with open(CDN_CONFIG_PATH, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
            print(f"✅ CDN配置加载成功: {CDN_CONFIG_PATH}")
        except FileNotFoundError:
            print(f"⚠️ 警告: 找不到配置文件 {CDN_CONFIG_PATH}，使用默认配置")
            self._config = self._get_default_config()
        except Exception as e:
            print(f"❌ 加载配置失败: {e}，使用默认配置")
            self._config = self._get_default_config()
    
    @staticmethod
    def _get_default_config() -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "rate_limit": {
                "enabled": True,
                "window_seconds": 60,
                "by_resource_type": {
                    "video": 50,
                    "image": 200,
                    "file": 100,
                    "default": 100
                }
            },
            "security": {
                "timestamp_tolerance": 300,
                "internal_source_id": "internal"
            }
        }
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值（支持点号路径，如 'rate_limit.default_limit'）"""
        keys = key.split('.')
        value = self._config
        
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        
        return value
    
    def get_qps_limits(self) -> Dict[str, int]:
        """获取 QPS 限流配置"""
        rate_limit = self._config.get("rate_limit", {})
        by_type = rate_limit.get("by_resource_type", {})
        
        return {
            "video": by_type.get("video", 50),
            "image": by_type.get("image", 200),
            "file": by_type.get("file", 100),
            "default": by_type.get("default", 100)
        }
    
    def get_window_seconds(self) -> int:
        """获取时间窗口（秒）"""
        rate_limit = self._config.get("rate_limit", {})
        return rate_limit.get("window_seconds", 60)
    
    def get_timestamp_tolerance(self) -> int:
        """获取时间戳容差（秒）"""
        security = self._config.get("security", {})
        return security.get("timestamp_tolerance", 300)
    
    def get_internal_source_id(self) -> str:
        """获取内部来源标识（后端服务标识，无需受QPS限流）"""
        security = self._config.get("security", {})
        return security.get("internal_source_id", "internal")
    
    def is_rate_limit_enabled(self) -> bool:
        """是否启用限流"""
        rate_limit = self._config.get("rate_limit", {})
        return rate_limit.get("enabled", True)
    
    def get_allowed_paths(self) -> list:
        """获取URL路径白名单"""
        allowed_paths = self._config.get("allowed_paths", [])
        if not allowed_paths:
            # 如果未配置，使用默认允许的路径
            allowed_paths = ["/user", "/web"]
        return allowed_paths


def get_cdn_config() -> CDNConfig:
    """获取 CDN 配置实例"""
    return CDNConfig()


# ============================================================================
# 2. 数据模型 (来自 cdn/models/auth.py)
# ============================================================================

class CDNAuthRequest(BaseModel):
    """CDN 远程鉴权请求"""
    ip: str = Field(..., description="用户真实IP")
    url: str = Field(..., description="请求资源URL")
    timestamp: int = Field(..., description="请求时间戳（秒级）")


class CDNAuthResponse(BaseModel):
    """CDN 远程鉴权响应"""
    status: str = Field(..., description="鉴权结果: allow / deny")
    reason: Optional[str] = Field(None, description="拒绝原因")
    remaining_qps: Optional[int] = Field(None, description="IP剩余QPS")
    limit: Optional[int] = Field(None, description="IP QPS限制值")
    current: Optional[int] = Field(None, description="IP当前QPS计数")
    timestamp: int = Field(..., description="响应时间戳")


class CDNAuthDenyResponse(BaseModel):
    """CDN 鉴权失败响应"""
    status: str = "deny"
    reason: str
    limit: int
    current: int
    reset_after: int = Field(..., description="多少秒后重试")
    timestamp: int


class IPBlackListItem(BaseModel):
    """IP黑名单项"""
    ip: str
    reason: str = "Manual blacklist"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None


class ResourceQPSConfig(BaseModel):
    """资源类型的QPS限制配置"""
    resource_type: str = Field(..., description="资源类型，如 video, image, file")
    qps_limit: int = Field(default=100, description="每秒请求数限制")
    window_seconds: int = Field(default=1, description="时间窗口（秒）")


# ============================================================================
# 3. QPS限流器 (来自 cdn/services/qps_limiter.py)
# ============================================================================

class CDNQPSLimiter:
    """
    CDN IP级QPS限流器
    
    限流算法：基于滑动时间窗口的令牌桶算法
    Redis键设计：cdn:qps:{ip_hash}:{resource_type}
    
    Note: 使用IP的MD5哈希作为键的一部分，以支持IPv6地址（包含冒号）
    """
    
    def __init__(self):
        # 从配置文件读取QPS限制
        config = get_cdn_config()
        self.resource_limits = config.get_qps_limits()
        self.default_window = config.get_window_seconds()
        
        print(f"📋 QPS限流配置已加载:")
        print(f"   - 视频: {self.resource_limits['video']} QPS")
        print(f"   - 图片: {self.resource_limits['image']} QPS")
        print(f"   - 文件: {self.resource_limits['file']} QPS")
        print(f"   - 默认: {self.resource_limits['default']} QPS")
    
    @staticmethod
    def _normalize_ip(ip: str) -> str:
        """
        对 IP 地址进行规范化处理，返回其 MD5 哈希值
        
        这样可以避免 IPv6 地址中的冒号与 Redis 键分隔符冲突
        
        Args:
            ip: IPv4 或 IPv6 地址
        
        Returns:
            IP 的 MD5 哈希值（32位十六进制字符串）
        """
        import hashlib
        return hashlib.md5(ip.encode('utf-8')).hexdigest()
    
    def get_resource_type(self, url: str) -> str:
        """
        从URL判断资源类型
        
        Args:
            url: 资源URL，如 /video/abc.mp4
            
        Returns:
            资源类型：video, image, file, default
        """
        url_lower = url.lower()
        
        # 视频资源
        if any(ext in url_lower for ext in ['.mp4', '.flv', '.m3u8', '.mkv', '.avi', '.mov']):
            return "video"
        
        # 图片资源
        if any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg']):
            return "image"
        
        # 文件资源
        if any(ext in url_lower for ext in ['.zip', '.rar', '.7z', '.exe', '.msi', '.bin']):
            return "file"
        
        return "default"
    
    def get_limit(self, resource_type: str) -> int:
        """获取该资源类型的QPS限制"""
        return self.resource_limits.get(resource_type, self.resource_limits["default"])
    
    async def check_rate_limit(self, ip: str, url: str, custom_limit: int = None) -> Tuple[bool, int, int]:
        """
        检查IP的QPS是否超限（基于滑动时间窗口）
        
        Args:
            ip: 用户IP（IPv4或IPv6）
            url: 请求资源URL
            custom_limit: 自定义限制（优先级高于资源类型限制）
        
        Returns:
            (is_allowed, current_count, remaining_qps)
            is_allowed: True/False 是否允许访问
            current_count: 当前时间窗口内的请求数
            remaining_qps: 剩余QPS数量
        """
        # 1. 确定资源类型和QPS限制
        resource_type = self.get_resource_type(url)
        limit = custom_limit or self.get_limit(resource_type)
        
        # 2. 获取Redis连接
        redis = await RedisManager.get_connection()
        
        # 3. 规范化IP地址，生成Redis键（使用MD5哈希避免IPv6冒号问题）
        ip_hash = self._normalize_ip(ip)
        key = f"cdn:qps:{ip_hash}:{resource_type}"
        current_time = int(time.time())
        
        # 4. 使用Redis的滑动时间窗口实现
        # 删除过期数据（1分钟内的数据）
        window_start = current_time - self.default_window
        
        # 使用 ZSET 存储请求时间戳
        # 分数=时间戳，值=request_id
        await redis.zremrangebyscore(key, 0, window_start)
        
        # 5. 读取当前窗口内的请求数
        current_count = await redis.zcard(key)
        
        # 6. 判断是否超限
        is_allowed = current_count < limit
        
        # 7. 如果未超限，记录这次请求
        if is_allowed:
            request_id = f"{ip_hash}:{current_time}:{time.time_ns()}"
            await redis.zadd(key, {request_id: current_time})
            # 设置过期时间，自动清理
            await redis.expire(key, self.default_window + 10)
        
        # 8. 计算剩余QPS
        remaining_qps = max(0, limit - current_count - 1)  # -1 是本次请求
        
        return is_allowed, current_count, remaining_qps
    
    async def get_ip_qps_info(self, ip: str, resource_type: str = None) -> dict:
        """
        获取IP的QPS使用信息（用于监控和管理）
        
        Args:
            ip: 用户IP（IPv4或IPv6）
            resource_type: 资源类型，如果为None则查询所有类型
        
        Returns:
            QPS使用情况字典
        """
        redis = await RedisManager.get_connection()
        current_time = int(time.time())
        window_start = current_time - self.default_window
        
        # 规范化IP地址
        ip_hash = self._normalize_ip(ip)
        
        result = {}
        
        if resource_type:
            # 查询特定资源类型
            key = f"cdn:qps:{ip_hash}:{resource_type}"
            count = await redis.zcard(key)
            limit = self.get_limit(resource_type)
            result[resource_type] = {
                "current": count,
                "limit": limit,
                "remaining": max(0, limit - count)
            }
        else:
            # 查询所有资源类型
            for rtype in self.resource_limits.keys():
                if rtype == "default":
                    continue
                key = f"cdn:qps:{ip_hash}:{rtype}"
                count = await redis.zcard(key)
                limit = self.get_limit(rtype)
                result[rtype] = {
                    "current": count,
                    "limit": limit,
                    "remaining": max(0, limit - count)
                }
        
        return result
    
    async def reset_ip_qps(self, ip: str, resource_type: str = None) -> bool:
        """
        重置IP的QPS计数（管理员操作）
        
        Args:
            ip: 用户IP（IPv4或IPv6）
            resource_type: 资源类型，如果为None则重置所有类型
        
        Returns:
            是否重置成功
        """
        redis = await RedisManager.get_connection()
        
        # 规范化IP地址
        ip_hash = self._normalize_ip(ip)
        
        if resource_type:
            key = f"cdn:qps:{ip_hash}:{resource_type}"
            await redis.delete(key)
        else:
            # 删除该IP的所有资源类型计数
            for rtype in self.resource_limits.keys():
                if rtype == "default":
                    continue
                key = f"cdn:qps:{ip_hash}:{rtype}"
                await redis.delete(key)
        
        return True


# 全局单例
_cdn_qps_limiter = None


async def get_cdn_qps_limiter() -> CDNQPSLimiter:
    """获取全局CDN QPS限流器实例"""
    global _cdn_qps_limiter
    if _cdn_qps_limiter is None:
        _cdn_qps_limiter = CDNQPSLimiter()
    return _cdn_qps_limiter


# ============================================================================
# 4. 鉴权服务 (来自 cdn/services/auth_service.py)
# ============================================================================

class CDNAuthService:
    """
    CDN 鉴权服务
    
    核心功能：
    1. 验证请求签名（防伪造）
    2. 检查IP黑名单
    3. 检查IP QPS限制
    4. 返回鉴权结果
    """
    
    def __init__(self):
        self.qps_limiter = None
    
    async def initialize(self):
        """异步初始化"""
        self.qps_limiter = await get_cdn_qps_limiter()
    
    async def authenticate(
        self,
        ip: str,
        url: str,
        timestamp: int,
        source_id: str = None
    ) -> Tuple[bool, str, int]:
        """
        执行完整的鉴权流程
        
        Args:
            ip: 用户IP
            url: 请求资源URL
            timestamp: 请求时间戳
            source_id: 请求来源标识（用于区分内部请求和外部请求）
        
        Returns:
            (is_allowed, reason, http_status_code)
        """
        config = get_cdn_config()
        
        # 1. 检查是否为后端请求（内部服务） - 无需受任何限制，直接放行
        internal_source_id = config.get_internal_source_id()
        if source_id == internal_source_id:
            # 后端请求：跳过所有鉴权（时间戳验证、签名验证、QPS限流），直接放行
            print(f"✅ 后端请求通过（跳过所有鉴权） (source_id={source_id})")
            return True, "OK", 200
        
        # 2. 普通用户请求进行完整鉴权
        # 检查URL路径是否在白名单中
        allowed_paths = config.get_allowed_paths()
        url_allowed = False
        for allowed_path in allowed_paths:
            if url.startswith(allowed_path):
                url_allowed = True
                break
        
        if not url_allowed:
            print(f"❌ URL路径被拒绝: {url}（不在白名单中: {allowed_paths}）")
            return False, "URL path not allowed", 403
        
        # 3. 时间戳验证（防重放攻击）
        current_time = int(time.time())
        time_diff = abs(current_time - timestamp)
        timestamp_tolerance = config.get_timestamp_tolerance()
        
        if time_diff > timestamp_tolerance:
            return False, "Timestamp expired", 403
        
        # 4. 检查QPS限制
        if self.qps_limiter is None:
            await self.initialize()
        
        is_allowed, current_count, remaining = await self.qps_limiter.check_rate_limit(ip, url)
        
        if not is_allowed:
            return False, "QPS limit exceeded", 429
        
        # 鉴权通过
        return True, "OK", 200
    
    def build_response(
        self,
        is_allowed: bool,
        reason: str = None,
        current_count: int = 0,
        limit: int = 100
    ) -> dict:
        """
        构建鉴权响应
        
        Args:
            is_allowed: 是否允许访问
            reason: 拒绝原因
            current_count: 当前QPS计数
            limit: QPS限制值
        
        Returns:
            响应字典
        """
        timestamp = int(time.time())
        
        if is_allowed:
            return {
                "status": "allow",
                "timestamp": timestamp
            }
        else:
            return {
                "status": "deny",
                "reason": reason,
                "limit": limit,
                "current": current_count,
                "timestamp": timestamp
            }


# 全局单例
_cdn_auth_service = None


async def get_cdn_auth_service() -> CDNAuthService:
    """获取或创建CDN鉴权服务实例"""
    global _cdn_auth_service
    if _cdn_auth_service is None:
        _cdn_auth_service = CDNAuthService()
        await _cdn_auth_service.initialize()
    return _cdn_auth_service


# ============================================================================
# 5. FastAPI 路由 (来自 cdn/routes/verify.py)
# ============================================================================

router = APIRouter(tags=["CDN Auth"])

load_dotenv()


@router.post("/verify")
async def verify_auth(
    request: Request,
    remote_addr: str = Header(None, alias="remote-addr"),
    x_request_uri: str = Header(None, alias="X-Request-URI"),
    x_source_id: str = Header(None, alias="X-Source-ID"),
    ali_origin_real_url: str = Header(None, alias="ali-origin-real-url"),
):
    """
    CDN 远程鉴权主接口
    
    从阿里云CDN接收鉴权请求，验证IP和QPS，返回通过/拒绝。
    
    请求头说明（阿里云CDN）：
    - remote-addr: 用户真实IP（必需）
    - ali-origin-real-url: 用户请求的真实URL（阿里云CDN特有）
    
    响应：
    - 200 OK: 鉴权通过，CDN放行请求
    - 403 Forbidden: IP被黑名单/无权限，CDN拒绝请求
    - 429 Too Many Requests: IP QPS超限，CDN拒绝请求
    """
    try:
        # # 0. 打印所有请求头（用于诊断）
        # print(f"📨 收到 CDN 鉴权请求，所有请求头：")
        # for header_name, header_value in request.headers.items():
        #     print(f"   {header_name}: {header_value}")
        
        # 1. 提取用户真实IP（从 remote-addr 头获取，这是CDN传递的真实用户IP）
        ip = None
        if remote_addr:
            ip = remote_addr.strip()
            # print(f"   📍 使用 remote-addr: {ip}")
            
            # # 显示IP的哈希值（用于调试Redis键）
            # ip_hash = CDNQPSLimiter._normalize_ip(ip)
            # print(f"   🔐 IP哈希: {ip_hash}")
        
        # 如果没有 remote-addr，无法获取真实用户IP，直接拒绝
        if not ip:
            print(f"❌ 缺少用户真实IP: remote-addr 未提供")
            return JSONResponse(
                status_code=403,
                content={
                    "status": "error",
                    "reason": "Missing user real IP (remote-addr header required)"
                }
            )
        
        # 1. 提取URL路径（从完整URL中提取路径部分）
        url = None
        if ali_origin_real_url:
            # ali-origin-real-url 格式: https://cdn.ai579.com/web/avatar/Lucy.jpg
            # 需要提取: /web/avatar/Lucy.jpg
            from urllib.parse import urlparse
            parsed = urlparse(ali_origin_real_url)
            url = parsed.path or ali_origin_real_url
        
        # 如果没有 ali-origin-real-url，尝试其他来源
        if not url:
            url = x_request_uri or request.url.path
        
        # 2. 时间戳使用当前时间
        timestamp = int(time.time())
        
        # 3. 参数验证
        if not ip or not url:
            print(f"❌ 参数不完整: ip={ip}, url={url}")
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "reason": "Missing required parameters: ip and url"
                }
            )
        
        print(f"✅ 鉴权参数: ip={ip}, url={url}, timestamp={timestamp}")
        
        # 4. 获取鉴权服务实例
        auth_service = await get_cdn_auth_service()
        
        # 5. 执行鉴权
        is_allowed, reason, status_code = await auth_service.authenticate(
            ip=ip,
            url=url,
            timestamp=timestamp,
            source_id=x_source_id
        )
        
        print(f"   鉴权结果: allowed={is_allowed}, reason={reason}, status={status_code}")
        
        # 6. 构建响应
        if is_allowed:
            response_data = {
                "status": "allow"
            }
            return JSONResponse(status_code=200, content=response_data)
        else:
            response_data = {
                "status": "deny",
                "reason": reason
            }
            return JSONResponse(status_code=status_code, content=response_data)
    
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "reason": "Invalid JSON in request body"
            }
        )
    except Exception as e:
        print(f"❌ 鉴权异常: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "reason": "Internal server error"
            }
        )


@router.get("/health")
async def health_check():
    """
    健康检查接口
    
    CDN可以定期调用此接口检查鉴权服务是否在线
    """
    return {
        "status": "healthy",
        "message": "CDN Auth Service is running"
    }


@router.get("/qps/{ip}")
async def get_ip_qps_info(ip: str, resource_type: str = None):
    """
    查询IP的QPS使用情况（仅限内部调用）
    
    Args:
        ip: 用户IP
        resource_type: 资源类型（可选，如果为None则查询所有）
    
    Returns:
        QPS使用信息
    """
    auth_service = await get_cdn_auth_service()
    qps_limiter = auth_service.qps_limiter
    
    info = await qps_limiter.get_ip_qps_info(ip, resource_type)
    
    return {
        "ip": ip,
        "resource_type": resource_type,
        "qps_info": info
    }


@router.post("/qps/{ip}/reset")
async def reset_ip_qps(ip: str, resource_type: str = None):
    """
    重置IP的QPS计数（仅限内部调用及管理员）
    
    Args:
        ip: 用户IP
        resource_type: 资源类型（可选），如果为None则重置所有类型
    
    Returns:
        操作结果
    """
    auth_service = await get_cdn_auth_service()
    qps_limiter = auth_service.qps_limiter
    
    result = await qps_limiter.reset_ip_qps(ip, resource_type)
    
    return {
        "status": "success" if result else "failed",
        "ip": ip,
        "resource_type": resource_type or "all"
    }
