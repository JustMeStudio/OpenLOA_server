import asyncio
from contextvars import ContextVar

# 使用 ContextVar 代替全局变量，确保异步并发安全
PROJECT_NAME: ContextVar[str] = ContextVar('PROJECT_NAME', default="")
PROJECT_FILE_NAME: ContextVar[str] = ContextVar('PROJECT_FILE_NAME', default="")
PROJECT_PATH: ContextVar[str] = ContextVar('PROJECT_PATH', default="")
PROJECT_FILE_PATH: ContextVar[str] = ContextVar('PROJECT_FILE_PATH', default="")

# 消息队列：用于在工具执行期间实时推送消息（如QR码截图）
# ⚠️ 重要：default=None 而不是 default=[]，避免可变对象共享导致跨会话消息污染
PENDING_MESSAGES: ContextVar[list] = ContextVar('PENDING_MESSAGES', default=None)

# 消息队列事件：用于告诉 background_queue_monitor 有新消息待处理
# 使用事件驱动代替轮询，大幅减少消息推送延迟
MESSAGE_ADDED_EVENT: ContextVar[asyncio.Event] = ContextVar('MESSAGE_ADDED_EVENT', default=None)


def add_pending_message(message: dict):
    """
    🚀 工具函数：添加消息到队列并触发事件通知
    
    在 Dante_tools.py 等工具文件中使用此函数替代直接操作 PENDING_MESSAGES，
    这样可以自动触发事件，使消息在 5-20ms 内被推送而不是等待 500ms
    
    用法：
        from agents.globals.context import add_pending_message
        add_pending_message({
            "role": "system_push",
            "type": "qr_code_update",
            "content": "...",
            "image_url": "..."
        })
    """
    # 获取当前队列
    current_messages = PENDING_MESSAGES.get()
    if not isinstance(current_messages, list):
        current_messages = []
    
    # 添加新消息
    current_messages.append(message)
    PENDING_MESSAGES.set(current_messages)
    
    # 触发事件，让 background_queue_monitor 立即处理
    event = MESSAGE_ADDED_EVENT.get()
    if event:
        event.set()
