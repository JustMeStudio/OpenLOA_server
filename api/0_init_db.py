import os
import sqlite3
import uuid
import hashlib
from datetime import datetime
from dotenv import load_dotenv
from fastapi import APIRouter

router = APIRouter()

load_dotenv()
DB_PATH = os.getenv("DB_PATH")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def column_exists(cursor, table_name: str, column_name: str) -> bool:
    """检查表中是否存在某个字段"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    return any(col[1] == column_name for col in columns)

def add_column_if_not_exists(cursor, table_name: str, column_name: str, column_type: str):
    """如果字段不存在，则添加字段"""
    if not column_exists(cursor, table_name, column_name):
        try:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
            print(f"✅ 添加字段: {table_name}.{column_name}")
        except sqlite3.OperationalError as e:
            print(f"⚠️ 添加字段失败: {table_name}.{column_name} - {e}")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. 用户基本信息表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_info (
            user_id TEXT PRIMARY KEY,
            email TEXT UNIQUE,       
            nick_name TEXT,
            avatar_url TEXT,
            bio TEXT,
            phone_number TEXT UNIQUE,
            gender TEXT,

            user_role TEXT,
            is_enabled INTEGER DEFAULT 1,

            email_verified INTEGER DEFAULT 0, -- 邮箱是否已验证（防止虚假注册）
            phone_number_verified INTEGER DEFAULT 0,
            ip_address TEXT,                  -- 用户注册或最后登录的 IP（用于风控/安全分析）
            language_pref TEXT DEFAULT 'en',  -- 用户界面语言偏好（多语言支持）
            timezone TEXT DEFAULT 'UTC',      -- 用户所在时区（方便给用户发通知时避开深夜）

            create_time TEXT DEFAULT (datetime('now')),
            last_login_time TEXT DEFAULT (datetime('now'))
        )
    ''')

    # 2. 安全与鉴权
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_password (
            user_id TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES user_info(user_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_token (
            user_id TEXT PRIMARY KEY,
            refresh_token TEXT,
            expire_time TEXT,
            FOREIGN KEY (user_id) REFERENCES user_info(user_id)
        )
    ''')

    # 创建对话话题表 (Conversations)
    # 用于存储对话的元数据，如标题、使用的模型等
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT,
            agent_name TEXT,
            is_pinned INTEGER DEFAULT 0, -- 是否置顶 (0:否, 1:是)
            create_time TEXT,
            update_time TEXT
        )
    ''')
    # 创建索引 (提升查询速度)
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_conv_user_id ON conversations (user_id)')

    # 创建消息明细表 (Messages)
    # 用于存储每一条具体的聊天内容
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,        -- 'user', 'assistant', 或 'system'
            content TEXT NOT NULL,     -- 消息正文
            tool_calls TEXT,             -- 存储 AI 发出的调用指令 (JSON 字符串)
            tool_call_id TEXT,           -- 存储该条消息作为“结果”时的关联 ID
            tokens_used INTEGER,       -- 该条消息消耗的 Token 数 (可选)
            create_time TEXT,
            FOREIGN KEY (conversation_id) REFERENCES conversations (conversation_id) ON DELETE CASCADE
        )
    ''')
    # 创建索引 (提升查询速度)
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_msg_conv_id ON messages (conversation_id)')

    # 用户反馈表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_feedback (
            feedback_id TEXT PRIMARY KEY,
            user_id TEXT,                        -- 可为空（支持匿名反馈）
            type TEXT,                           -- 'bug' | 'suggestion' | 'complaint' | 'other'
            content TEXT NOT NULL,               -- 反馈正文
            contact TEXT,                        -- 用户留的联系方式（可选）
            status TEXT DEFAULT 'pending',       -- 'pending' | 'reviewed' | 'resolved'
            admin_note TEXT,                     -- 管理员备注
            create_time TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES user_info(user_id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_feedback_user_id ON user_feedback (user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_feedback_status ON user_feedback (status)')

    conn.commit()
    conn.close()
    print("✅ Database Initialized Successfully.")

init_db()

def init_admin_user():
    """如果数据库中不存在指定邮箱的账户，则根据环境变量创建一个初始管理员。"""
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if not admin_email or not admin_password:
        print("⚠️ 未配置 ADMIN_EMAIL 或 ADMIN_PASSWORD，跳过初始管理员创建。")
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM user_info WHERE email = ?", (admin_email,))
    if cursor.fetchone():
        print(f"ℹ️ 管理员账户 {admin_email} 已存在，跳过创建。")
        conn.close()
        return
    user_id = str(uuid.uuid4())
    password_hash = hashlib.sha256(admin_password.encode()).hexdigest()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """INSERT INTO user_info
           (user_id, email, nick_name, user_role, is_enabled, email_verified, create_time, last_login_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, admin_email, "Admin", "admin", 1, 1, now, now)
    )
    cursor.execute(
        "INSERT INTO user_password (user_id, password_hash) VALUES (?, ?)",
        (user_id, password_hash)
    )
    conn.commit()
    conn.close()
    print(f"✅ 初始管理员账户已创建: {admin_email}（请立即修改默认密码）")

init_admin_user()