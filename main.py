# 本地
# uvicorn main:app --host 0.0.0.0 --port 9000 --reload
# uvicorn main:app --host 0.0.0.0 --port 9000 --workers 3
# 服务器
# nohup uvicorn main:app --host 0.0.0.0 --port 9000 --workers 3 &

import os
import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from importlib import import_module
from utils.config_loader import init_config, validate_config
from utils.redis_manager import RedisManager
from utils.generation_manager import rebuild_semaphores
from utils.ip_rate_limiter import get_ip_limiter
from utils.user_rate_limiter import get_user_limiter
from utils.config_loader import get_rate_limit_rule, get_user_rate_limit_rule


load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "")


# 应用启动和关闭的生命周期钩子
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ===== 启动事件 =====
    print("🚀 应用启动中...")
    # 1. 初始化配置
    await init_config()
    # 2. 验证配置
    if not await validate_config():
        print("⚠️ 配置验证失败，请检查rate_limit.yaml")
    # 3. 根据配置创建信号量
    await rebuild_semaphores()
    print("✅ 应用启动完成！")
    yield  # 应用运行中...
    # ===== 关闭事件 =====
    print("🛑 应用关闭中...")
    # 优雅关闭时清理全局 Redis 连接池
    await RedisManager.disconnect()
    print("✅ 应用已关闭")


# 创建FastAPI实例
app = FastAPI(lifespan=lifespan)

# 允许所有跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],             # 允许所有源（域名、IP、端口）
    allow_credentials=True,          # 允许携带 Cookie 和身份验证凭据
    allow_methods=["*"],             # 允许所有 HTTP 方法 (GET, POST, OPTIONS 等)
    allow_headers=["*"],             # 允许所有请求头
    expose_headers=["*"],            # 允许前端访问所有响应头（这对 SSE 流式非常重要）
)

# ===== IP级限流中间件 =====
@app.middleware("http")
async def ip_rate_limit_middleware(request: Request, call_next):
    """
    IP级限流中间件 - 防止恶意连续调用和暴力破解
    检查规则：先查询endpoint特定规则，再用default规则
    """
    # 1. 获取客户端IP（支持代理）
    client_ip = request.client.host
    if request.headers.get("X-Forwarded-For"):
        # 在反向代理环境下，取第一个IP
        client_ip = request.headers.get("X-Forwarded-For").split(",")[0].strip()
    # 2. 获取请求端点
    endpoint = request.url.path
    # 3. 从配置获取该端点的限流规则
    rule = await get_rate_limit_rule(endpoint)
    # 4. 如果配置中无该端点或未启用限流，直接通过
    if rule is None:
        return await call_next(request)
    # 5. 执行限流检查
    limiter = await get_ip_limiter()
    limit = rule["limit"]
    window = rule["window_seconds"]
    rule_name = rule.get("rule_name", "unknown")
    allow, current_count, remaining = await limiter.check_rate_limit(
        client_ip, 
        endpoint, 
        limit, 
        window
    )
    # 6. 如果超限，返回429错误
    if not allow:
        return JSONResponse(
            status_code=429,
            content={
                "status": "error",
                "detail": f"Too many requests. Rate limit ({limit} requests per {window}s) exceeded.",
                "rule": rule_name,
                "current_count": current_count,
                "limit": limit,
                "window_seconds": window
            },
            headers={
                "Retry-After": str(window),  # 告诉客户端多少秒后再试
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(max(0, remaining)),
                "X-RateLimit-Reset": str(window)
            }
        )
    # 7. 未超限，添加限流信息到response headers
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(window)
    return response


# ===== 用户级限流中间件 =====
@app.middleware("http")
async def user_rate_limit_middleware(request: Request, call_next):
    """
    用户级限流中间件 - 防止单个用户异常使用
    仅对已认证请求生效（需要有效JWT token）
    """
    endpoint = request.url.path
    
    # 1. 尝试从Authorization header提取JWT token
    # 注意：检查所有可能的大小写变体
    auth_header = (
        request.headers.get("Authorization", "") or 
        request.headers.get("authorization", "") or
        request.headers.get("AUTHORIZATION", "")
    )
    
    if not auth_header:
        return await call_next(request)
    
    # 使用 case-insensitive 比较
    if not auth_header.lower().startswith("bearer "):
        return await call_next(request)
    
    token = auth_header.replace("Bearer ", "").replace("bearer ", "").strip()
    
    # 2. 解析JWT获取user_id
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user_id: str = payload.get("user_id")
        if not user_id:
            # token有效但无user_id，直接通过
            return await call_next(request)
    except jwt.ExpiredSignatureError:
        # token已过期，跳过用户级限流
        return await call_next(request)
    except jwt.InvalidTokenError as e:
        # token无效，跳过用户级限流
        return await call_next(request)
    except Exception as e:
        # 其他异常，跳过用户级限流
        return await call_next(request)
    # 3. 获取请求端点
    endpoint = request.url.path
    # 4. 从配置获取该端点的用户级限流规则
    rule = await get_user_rate_limit_rule(endpoint)
    # 5. 如果配置中无该端点或未启用用户级限流，直接通过
    if rule is None:
        return await call_next(request)
    
    # 6. 执行用户级限流检查
    limiter = await get_user_limiter()
    limit = rule["limit"]
    window = rule["window_seconds"]
    rule_name = rule.get("rule_name", "unknown")
    
    allow, current_count, remaining = await limiter.check_rate_limit(
        user_id, 
        endpoint, 
        limit, 
        window
    )
    # 7. 如果超限，返回429错误
    if not allow:
        return JSONResponse(
            status_code=429,
            content={
                "status": "error",
                "detail": f"User rate limit exceeded. Maximum {limit} requests per {window}s.",
                "rule": rule_name,
                "current_count": current_count,
                "limit": limit,
                "window_seconds": window
            },
            headers={
                "Retry-After": str(window),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(max(0, remaining)),
                "X-RateLimit-Reset": str(window)
            }
        )
    
    # 8. 未超限，添加限流信息到response headers
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(window)
    response.headers["X-User-RateLimit-Limit"] = str(limit)
    return response


# 动态加载所有API模块
module_dir = os.path.join(os.path.dirname(__file__), "api")
for fname in os.listdir(module_dir):
    if fname.endswith(".py") and fname != "__init__.py":
        mod = import_module(f"api.{fname[:-3]}")
        if hasattr(mod, "router"):
            app.include_router(mod.router, prefix=f"/{fname[:-3]}")