import os
import re
import aiosqlite
import random
import string
import uuid
import hashlib
import jwt
import secrets
import yaml
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends, Request, BackgroundTasks
from pydantic import BaseModel, EmailStr, Field, validator
from typing import Annotated, Optional
from dotenv import load_dotenv
from utils.redis_manager import RedisManager
from .security import get_current_user, get_current_admin

router = APIRouter()

load_dotenv()
DB_PATH = os.getenv("DB_PATH")
SECRET_KEY = os.getenv("SECRET_KEY")
REFRESH_TOKEN_EXPIRE_DAYS = float(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS"))
ACCESS_TOKEN_EXPIRE_HOURS = float(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS"))

# 加载邮件服务配置
EMAIL_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "configs", "email_config.yaml")
with open(EMAIL_CONFIG_PATH, "r", encoding="utf-8") as f:
    EMAIL_CONFIG = yaml.safe_load(f)

# --- 工具函数 ---
async def get_db():
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    return conn

def hash_password(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

def validate_email_whitelist(email: str) -> bool:
    """
    验证邮箱是否在白名单中
    
    返回：True 表示通过验证，False 表示不在白名单中
    """
    if not EMAIL_CONFIG.get("whitelist_enabled", True):
        return True  # 如果未启用白名单，所有邮箱都通过
    
    # 提取邮箱域名
    try:
        domain = email.lower().split("@")[1]
    except IndexError:
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    # 检查黑名单（优先级最高）
    blacklist = EMAIL_CONFIG.get("blacklist", {}) or {}
    
    # 检查具体邮箱黑名单
    individual_emails = blacklist.get("individual_emails") or {}
    if email.lower() in individual_emails.keys():
        raise HTTPException(status_code=403, detail="This email address is not allowed to register")
    
    # 检查邮箱域名黑名单
    blacklist_domains = blacklist.get("domains") or {}
    if domain in blacklist_domains.keys():
        raise HTTPException(status_code=403, detail="This email domain is not allowed to register")
    
    # 检查白名单
    whitelist = EMAIL_CONFIG.get("whitelist", {}) or {}
    
    # 遍历所有白名单分类
    for category, domains in whitelist.items():
        if isinstance(domains, dict) and domain in domains.keys():
            return True
    
    # 不在白名单中
    error_message = EMAIL_CONFIG.get("whitelist_error_message", "Email domain is not allowed")
    raise HTTPException(status_code=400, detail=error_message)

async def verify_code(target: str, code: str, purpose: str) -> bool:
    """验证码校验（从Redis读取）"""
    redis_key = f"code:{purpose}:{target}"
    try:
        cache_manager = RedisManager()
        stored_code = await cache_manager.get(redis_key)
        if stored_code is None:
            raise HTTPException(status_code=400, detail="Verification code expired or not requested")
        if stored_code != code:
            return False
        # 验证成功后删除
        await cache_manager.delete(redis_key)
        return True
    except HTTPException:
        raise
    except Exception as e:
        print(f"⚠️ 验证码校验失败: {e}")
        raise HTTPException(status_code=500, detail="Code verification error")

def send_email_task(target_email: str, code: str, purpose: str = "register"):
    """
    发送邮件的任务，根据配置选择阿里云 SMTP 或 mock 模式
    purpose: 'register' 注册验证码 | 'reset' 找回密码验证码
    """
    provider = EMAIL_CONFIG.get("provider", "aliyun")

    if provider == "mock":
        # mock 模式：不发送邮件，直接打印日志即可
        fixed_code = EMAIL_CONFIG.get("mock", {}).get("fixed_code", "123456")
        print(f"[Mock] 验证码已模拟发送至 {target_email}，固定验证码: {fixed_code}")
        return

    # 构建邮件文案
    if purpose == "reset":
        subject = "账户安全验证"
        message_html = f"""
    <!DOCTYPE html>
    <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0; }}
                .header h1 {{ margin: 0; font-size: 28px; font-weight: 600; }}
                .content {{ background: #f8f9fa; padding: 40px 30px; text-align: center; }}
                .code-box {{ 
                    background: white; 
                    border: 2px solid #667eea; 
                    border-radius: 8px; 
                    padding: 30px; 
                    margin: 30px 0; 
                    display: inline-block;
                }}
                .verification-code {{ 
                    font-size: 48px; 
                    font-weight: 700; 
                    color: #667eea; 
                    letter-spacing: 8px;
                    font-family: 'Courier New', monospace;
                }}
                .footer {{ background: #f0f2f5; padding: 20px; font-size: 12px; color: #666; text-align: center; border-radius: 0 0 8px 8px; line-height: 1.6; }}
                .warning {{ color: #666; font-size: 13px; margin-top: 20px; }}
                p {{ margin: 15px 0; line-height: 1.6; color: #333; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🔐 账户安全验证</h1>
                </div>
                <div class="content">
                    <p>您的账户密码重置请求已被接收。</p>
                    <p style="font-size: 14px; color: #666;">请使用下方验证码完成重置流程：</p>
                    <div class="code-box">
                        <div class="verification-code">{code}</div>
                    </div>
                    <p class="warning">⏱️ 此验证码 5 分钟内有效</p>
                    <p class="warning">⚠️ 请勿将验证码分享给任何人</p>
                    <p style="font-size: 12px; color: #999; margin-top: 30px;">如果此操作不是由您发起，请立即忽略此邮件并确保账户安全。</p>
                </div>
                <div class="footer">
                    <p>© 2026 All Rights Reserved. | This is an automated message, please do not reply.</p>
                </div>
            </div>
        </body>
    </html>
    """
    else:
        subject = "验证您的账户"
        message_html = f"""
    <!DOCTYPE html>
    <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #00d4ff 0%, #0099ff 100%); color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0; }}
                .header h1 {{ margin: 0; font-size: 28px; font-weight: 600; }}
                .content {{ background: #f8f9fa; padding: 40px 30px; text-align: center; }}
                .code-box {{ 
                    background: white; 
                    border: 2px solid #00d4ff; 
                    border-radius: 8px; 
                    padding: 30px; 
                    margin: 30px 0; 
                    display: inline-block;
                }}
                .verification-code {{ 
                    font-size: 48px; 
                    font-weight: 700; 
                    color: #0099ff; 
                    letter-spacing: 8px;
                    font-family: 'Courier New', monospace;
                }}
                .footer {{ background: #f0f2f5; padding: 20px; font-size: 12px; color: #666; text-align: center; border-radius: 0 0 8px 8px; line-height: 1.6; }}
                .tips {{ color: #666; font-size: 13px; margin-top: 20px; }}
                p {{ margin: 15px 0; line-height: 1.6; color: #333; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>✅ 账户激活</h1>
                </div>
                <div class="content">
                    <p>欢迎加入我们的平台！</p>
                    <p style="font-size: 14px; color: #666;">请使用下方验证码完成账户激活：</p>
                    <div class="code-box">
                        <div class="verification-code">{code}</div>
                    </div>
                    <p class="tips">⏱️ 此验证码有效期为 5 分钟</p>
                    <p class="tips">🔒 此验证码仅供您本人使用</p>
                    <p style="font-size: 12px; color: #999; margin-top: 30px;">如果这不是您的注册操作，可以安全地忽略此邮件。</p>
                </div>
                <div class="footer">
                    <p>© 2026 All Rights Reserved. | This is an automated message, please do not reply.</p>
                </div>
            </div>
        </body>
    </html>
    """

    _send_email_aliyun(target_email, subject, message_html)


def _send_email_aliyun(target_email: str, subject: str, message_html: str):
    """使用阿里云 SMTP 发送邮件"""
    try:
        aliyun_cfg = EMAIL_CONFIG.get("aliyun", {})
        smtp_server = aliyun_cfg.get("smtp_server", "smtpdm.aliyun.com")
        smtp_port = aliyun_cfg.get("smtp_port", 465)
        sender_email = aliyun_cfg.get("sender_email", "")
        from_name = aliyun_cfg.get("from_name", "OpenLOA")
        smtp_password = os.getenv("ALIYUN_SMTP_PASSWORD", "")

        msg = MIMEMultipart()
        msg["From"] = f"{from_name} <{sender_email}>"
        msg["To"] = target_email
        msg["Subject"] = subject
        msg.attach(MIMEText(message_html, "html", "utf-8"))

        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(sender_email, smtp_password)
            server.sendmail(sender_email, [target_email], msg.as_string())
        print(f"[Aliyun] 成功向 {target_email} 发送邮件")
        return True
    except Exception as e:
        print(f"[Aliyun] 邮件发送失败: {str(e)}")
        return None

# 定义一个生成短随机字符串的函数
def generate_random_nickname():
    # 生成 6 位包含字母和数字的随机字符串
    chars = string.ascii_letters + string.digits
    return "user_"+"".join(secrets.choice(chars) for _ in range(6))

# --- Pydantic 模型 ---

class UserRegister(BaseModel):
    email: Optional[EmailStr] = None
    email_code: Optional[str] = None # 邮箱验证码
    phone_number: Optional[str] = None
    sms_code: Optional[str] = None   # 短信验证码
    password: str
    confirm_password: str
    nick_name: Optional[str] = Field(default_factory=generate_random_nickname)
    timezone: str = "Asia/Shanghai"            # 前端获取的时区
    language_pref: str = "en"

    @validator('phone_number')
    def validate_phone(cls, v):
        if v:
            v = v.replace(" ", "").replace("-", "")
            if not re.match(r"^\+?\d{7,15}$", v):
                raise ValueError("Invalid phone number format")
        return v

class UserLogin(BaseModel):
    account: str 
    password: str

class UpdatePassword(BaseModel):
    account: str
    old_password: str
    new_password: str
    confirm_new_password: str

class UpdateUserInfo(BaseModel):
    nick_name: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    gender: Optional[str] = None
    language_pref: Optional[str] = None
    timezone: Optional[str] = None

class ManagePermissions(BaseModel):
    user_id: str
    new_role: Optional[str] = None
    new_enabled: Optional[bool] = None

class ResetPassword(BaseModel):
    email: EmailStr
    email_code: str
    new_password: str
    confirm_new_password: str

# --- 接口实现 ---

@router.post("/send_code")
async def send_code(target: str, mode: str, purpose: str, background_tasks: BackgroundTasks):
    """发送验证码（存入Redis）"""
    if mode not in ('email', 'sms'):
        raise HTTPException(status_code=400, detail="Invalid mode, must be 'email' or 'sms'")
    if purpose not in ('register', 'reset'):
        raise HTTPException(status_code=400, detail="Invalid purpose, must be 'register' or 'reset'")
    
    # 邮箱白名单验证（仅对邮箱模式且用于注册时检查）
    # 对于找回密码（reset），由于用户邮箱已存在，所以允许绕过白名单
    if mode == 'email' and purpose == 'register':
        validate_email_whitelist(target)
    
    code = "".join(random.choices(string.digits, k=6))
    
    # mock 模式：使用固定验证码，不实际发送邮件
    provider = EMAIL_CONFIG.get("provider", "aliyun")
    if provider == "mock":
        code = str(EMAIL_CONFIG.get("mock", {}).get("fixed_code", "123456"))
    
    redis_key = f"code:{purpose}:{target}"
    try:
        cache_manager = RedisManager()
        # 存入Redis，5分钟过期
        await cache_manager.set(redis_key, code, expire=300)
        if provider != "mock":
            background_tasks.add_task(send_email_task, target, code, purpose)
        else:
            print(f"[Mock] 跳过邮件发送，固定验证码 '{code}' 已写入 Redis: {redis_key}")
        return {"message": "Verification code sent (Stored in Redis.)"}
    except Exception as e:
        print(f"⚠️ 发送验证码失败: {e}")
        raise HTTPException(status_code=500, detail="Failed to send verification code")


# 找回密码接口
@router.post("/reset_password")
async def reset_password(req: ResetPassword):
    if req.new_password != req.confirm_new_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    if not await verify_code(req.email, req.email_code, 'reset'):
        raise HTTPException(status_code=400, detail="Invalid email verification code")
    conn = await get_db()
    cursor = await conn.cursor()
    try:
        await cursor.execute("SELECT user_id FROM user_info WHERE email = ?", (req.email,))
        user = await cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Email not registered")
        user_id = user['user_id']
        await cursor.execute(
            "UPDATE user_password SET password_hash = ? WHERE user_id = ?",
            (hash_password(req.new_password), user_id)
        )
        # 强制登出，清空已有 Token
        await cursor.execute("DELETE FROM user_token WHERE user_id = ?", (user_id,))
        await conn.commit()
        return {"message": "Password reset successfully"}
    except HTTPException:
        raise
    except Exception as e:
        await conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await conn.close()


# 1. 注册接口 (含验证码校验)
@router.post("/register")
async def register(req: UserRegister, request: Request):
    # 基础校验
    if not req.email and not req.phone_number:
        raise HTTPException(status_code=400, detail="Either email or phone number is required")
    if req.password != req.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    
    # 邮箱白名单验证（如果提供了邮箱）
    if req.email:
        validate_email_whitelist(req.email)
    
    # 验证码校验逻辑
    if req.email:
        if not await verify_code(req.email, req.email_code, 'register'):
            raise HTTPException(status_code=400, detail="Invalid email verification code")
    if req.phone_number:
        if not await verify_code(req.phone_number, req.sms_code, 'register'):
            raise HTTPException(status_code=400, detail="Invalid SMS verification code")
    conn = await get_db()
    cursor = await conn.cursor()
    try:
        # 唯一性检查
        await cursor.execute("SELECT 1 FROM user_info WHERE email = ? OR (phone_number = ? AND phone_number IS NOT NULL)", 
                       (req.email, req.phone_number))
        if await cursor.fetchone():
            raise HTTPException(status_code=409, detail="Account already exists")
        user_id = str(uuid.uuid4())
        ip_addr = request.client.host # 获取注册 IP
        now_time = datetime.now(timezone.utc).isoformat()
        
        # 插入基本信息
        await cursor.execute('''
            INSERT INTO user_info (
                user_id, email, phone_number, nick_name, user_role, 
                email_verified, phone_number_verified, ip_address, 
                timezone, language_pref,
                create_time, last_login_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id, req.email, req.phone_number, req.nick_name, "user",
            1 if req.email else 0, 1 if req.phone_number else 0,
            ip_addr, req.timezone, req.language_pref, now_time, now_time
        ))
        # 插入密码
        await cursor.execute('INSERT INTO user_password (user_id, password_hash) VALUES (?, ?)',
                       (user_id, hash_password(req.password)))
        await conn.commit()
        return {
            "message": "User registered successfully",
            "user_id": user_id
        }
    except Exception as e:
        await conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await conn.close()


# 2. 登录接口
@router.post("/login")
async def login(req: UserLogin, request: Request):
    conn = await get_db()
    cursor = await conn.cursor()
    try:
        # 1. 在 SELECT 中增加 u.nick_name
        await cursor.execute('''
            SELECT u.user_id, u.is_enabled, u.nick_name, p.password_hash 
            FROM user_info u
            JOIN user_password p ON u.user_id = p.user_id
            WHERE u.email = ? OR u.phone_number = ?
        ''', (req.account, req.account))
        user = await cursor.fetchone()
        if not user or hash_password(req.password) != user['password_hash']:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not user['is_enabled']:
            raise HTTPException(status_code=403, detail="Account disabled")
        user_id = user['user_id']
        nick_name = user['nick_name']  # 2. 获取昵称变量
        now_time = datetime.now(timezone.utc).isoformat()
        # 更新最后登录时间和 IP
        await cursor.execute("UPDATE user_info SET last_login_time = ?, ip_address = ? WHERE user_id = ?", 
                       (now_time, request.client.host, user_id))
        # Token 生成
        refresh_token = ''.join(random.choices(string.ascii_letters + string.digits, k=64))
        expire_time = (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
        await cursor.execute('''
            INSERT INTO user_token (user_id, refresh_token, expire_time) 
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET refresh_token=excluded.refresh_token, expire_time=excluded.expire_time
        ''', (user_id, refresh_token, expire_time))
        access_token = jwt.encode({
            'user_id': user_id,
            'exp': datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
        }, SECRET_KEY, algorithm='HS256')
        await conn.commit()
        # 3. 在返回结果中加入 nick_name
        return {
            "access_token": access_token, 
            "refresh_token": refresh_token, 
            "token_type": "bearer",
            "nick_name": nick_name
        }
    finally:
        await conn.close()

# 3. 修改密码
@router.post("/update_password")
async def update_password(req: UpdatePassword, current_user_id: Annotated[str, Depends(get_current_user)]):
    conn = await get_db()
    cursor = await conn.cursor()
    try:
        # 定位账户并验证所属权
        await cursor.execute("SELECT user_id FROM user_info WHERE email = ? OR phone_number = ?", (req.account, req.account))
        user = await cursor.fetchone()
        if not user or user['user_id'] != current_user_id:
            raise HTTPException(status_code=403, detail="Account mismatch")
        # 验证旧密码
        await cursor.execute("SELECT password_hash FROM user_password WHERE user_id = ?", (current_user_id,))
        user_pwd = await cursor.fetchone()
        if user_pwd['password_hash'] != hash_password(req.old_password):
            raise HTTPException(status_code=401, detail="Old password incorrect")
        # 更新密码
        await cursor.execute("UPDATE user_password SET password_hash = ? WHERE user_id = ?", 
                       (hash_password(req.new_password), current_user_id))
        # 强制登出（清空 Token）
        await cursor.execute("DELETE FROM user_token WHERE user_id = ?", (current_user_id,))
        await conn.commit()
        return {"message": "Password updated successfully"}
    finally:
        await conn.close()

# 查看用户个人信息
@router.get("/profile")
async def get_user_profile(current_user_id: Annotated[str, Depends(get_current_user)]):
    conn = await get_db()
    cursor = await conn.cursor()
    try:
        await cursor.execute('''
            SELECT 
                nick_name, avatar_url, bio, gender
            FROM user_info 
            WHERE user_id = ?
        ''', (current_user_id,))
        
        user = await cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
            
        return dict(user)
    finally:
        await conn.close()


# 查看用户设置
@router.get("/settings")
async def get_user_settings(current_user_id: Annotated[str, Depends(get_current_user)]):
    conn = await get_db()
    cursor = await conn.cursor()
    try:
        await cursor.execute('''
            SELECT 
                email, email_verified, 
                phone_number, phone_number_verified,
                language_pref, timezone, user_role,
                create_time, last_login_time
            FROM user_info 
            WHERE user_id = ?
        ''', (current_user_id,))
        
        user = await cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return dict(user)
    finally:
        await conn.close()

# 用户资产查询
@router.get("/assets")
async def get_user_assets(current_user_id: Annotated[str, Depends(get_current_user)]):
    conn = await get_db()
    cursor = await conn.cursor()
    try:
        await cursor.execute('''
            SELECT user_role
            FROM user_info 
            WHERE user_id = ?
        ''', (current_user_id,))
        user = await cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return dict(user)
    finally:
        await conn.close()


# 4. 修改个人信息 (包含 bio, avatar_url, timezone 等)
@router.post("/update_user_info")
async def update_info(req: UpdateUserInfo, current_user_id: Annotated[str, Depends(get_current_user)]):
    conn = await get_db()
    cursor = await conn.cursor()
    try:
        # 动态构建更新 SQL
        fields = {k: v for k, v in req.dict(exclude={"user_id"}).items() if v is not None}
        if not fields:
            raise HTTPException(status_code=400, detail="Nothing to update")
        query = "UPDATE user_info SET " + ", ".join([f"{k} = ?" for k in fields.keys()]) + " WHERE user_id = ?"
        await cursor.execute(query, list(fields.values()) + [current_user_id])
        await conn.commit()
        return {"message": "Information updated"}
    finally:
        await conn.close()


# 5. 管理员权限控制
@router.post("/manage_permissions")
async def manage_perms(
    req: ManagePermissions, 
    admin_id: Annotated[str, Depends(get_current_admin)]
):
    conn = await get_db()
    cursor = await conn.cursor()
    try:
        updates = []
        params = []
        if req.new_role:
            updates.append("user_role = ?")
            params.append(req.new_role)
        if req.new_enabled is not None:
            updates.append("is_enabled = ?")
            params.append(1 if req.new_enabled else 0)
        if not updates:
            raise HTTPException(status_code=400, detail="No changes provided")
        params.append(req.user_id)
        await cursor.execute(f"UPDATE user_info SET {', '.join(updates)} WHERE user_id = ?", params)
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found")
        await conn.commit()
        return {"message": "Permissions updated"}
    finally:
        await conn.close()