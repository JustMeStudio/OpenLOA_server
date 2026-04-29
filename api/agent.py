import os
import aiosqlite
import uuid
import asyncio
import json
import importlib
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from typing import List, Optional, Annotated
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .security import get_current_user, get_current_admin 
from dotenv import load_dotenv

from utils.generation_manager import acquire_slot, release_slot, set_stop_flag
from utils.yaml_manager import get_profiles_manager


#-------------------------------------------------------------
router = APIRouter()

load_dotenv()
DB_PATH = os.getenv("DB_PATH")

# 自动扫描和导入agents
def _load_agents():
    """动态加载agents目录下的所有agent模块"""
    agent_map = {}
    agents_dir = Path(__file__).parent.parent / "agents"
    # 扫描agents目录下的.py文件
    for file_path in sorted(agents_dir.glob("*.py")):
        # 跳过__init__.py和其他以_开头的文件
        if file_path.name.startswith("_"):
            continue
        module_name = file_path.stem  # 文件名不含.py
        try:
            # 动态导入模块
            module = importlib.import_module(f"agents.{module_name}")
            # 尝试获取同名的函数或类
            if hasattr(module, module_name):
                agent_class = getattr(module, module_name)
                agent_map[module_name] = agent_class
                print(f"✅ 已加载 Agent: {module_name}")
            else:
                print(f"⚠️ 模块 {module_name} 中找不到同名函数/类")
        except Exception as e:
            print(f"❌ 加载 Agent {module_name} 失败: {e}")
    return agent_map

AGENT_MAP = _load_agents()
# 内容示例：{"Lucy": Lucy, "Lucy": Lucy,......}


#--------------------------------------------------------------------------------------
# agent对话交互相关接口
#--------------------------------------------------------------------------------------

async def get_history_from_db(conversation_id: str) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row 
        cursor = await conn.execute('''
            SELECT role, content, tool_calls, tool_call_id 
            FROM messages 
            WHERE conversation_id = ? 
            ORDER BY create_time ASC
        ''', (conversation_id,))
        rows = await cursor.fetchall()
    history = []
    for row in rows:
        msg = {"role": row["role"]}
        raw_content = row["content"]
        if raw_content:
            # --- 核心处理逻辑 ---
            # 判断是否是 JSON 格式（以 [ 开头）
            if raw_content.strip().startswith('['):
                try:
                    # 尝试还原为 list 或 dict
                    msg["content"] = json.loads(raw_content)
                except json.JSONDecodeError:
                    # 如果解析失败（例如内容恰好以 [ 开头但不是 JSON），退回到原始字符串
                    msg["content"] = raw_content
            else:
                # 纯文本字符串，直接赋值
                msg["content"] = raw_content
        if row["tool_calls"]: 
            msg["tool_calls"] = json.loads(row["tool_calls"])
        if row["tool_call_id"]: 
            msg["tool_call_id"] = row["tool_call_id"]
        history.append(msg)
    return history


async def save_message_to_db(conversation_id, role, content=None, tool_calls=None, tool_call_id=None):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute('''
            INSERT INTO messages (message_id, conversation_id, role, content, tool_calls, tool_call_id, create_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(uuid.uuid4()), 
            conversation_id, 
            role, 
            content, 
            tool_calls, # 这里存的是 JSON 字符串
            tool_call_id, 
            datetime.now().isoformat()
        ))
        await conn.commit()

# 辅助函数：在 conversations 表中创建新记录
async def create_new_conversation(user_id: str, first_msg: str, agent_name: str):
    async with aiosqlite.connect(DB_PATH) as conn:
        # 创建conversation_id
        conversation_id = str(uuid.uuid4())
        # 截取用户第一句话的前 15 个字作为默认标题
        title = first_msg[:15] + "..." if len(first_msg) > 15 else first_msg
        now = datetime.now().isoformat()
        await conn.execute('''
            INSERT INTO conversations (conversation_id, user_id, title, agent_name, create_time, update_time)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (conversation_id, user_id, title, agent_name, now, now))
        await conn.commit()
    return conversation_id, title


async def check_conversation_availability( conversation_id: str, user_id: str):
    """
    校验对话 ID是否存在，并且查看用户是否匹配
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        # 使用 Row 对象方便通过列名访问，或者直接取索引
        cursor = await conn.execute(
            "SELECT user_id FROM conversations WHERE conversation_id = ?", 
            (conversation_id,)
        )
        row = await cursor.fetchone()
    
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"对话 ID {conversation_id} 不存在。"
        )
    # row[0] 是 user_id
    if row[0] != user_id:
        raise HTTPException(
            status_code=403,
            detail="您没有权限访问此对话。"
        )


#--------------------------------------------------------------------------------------------

class chat_with_agent_class(BaseModel):
    agent_name: str
    content: str
    file_attachments: list[str] = [] # url列表
    conversation_id: str | None = None

#与agent对话接口
@router.post("/chat")
async def chat_with_agent(
    request: chat_with_agent_class,
    current_user_id: Annotated[str, Depends(get_current_user)]
):
    agent_name = request.agent_name
    user_content = request.content
    file_attachments = request.file_attachments
    conversation_id = request.conversation_id

    # 0. 检查 agent 是否存在
    agent_class = AGENT_MAP.get(agent_name)
    if not agent_class:
        raise HTTPException(status_code=400, detail=f"Agent '{agent_name}' 不存在")

    # 1. 确定对话 ID 及其合法性
    if not conversation_id:
        # 情况 A: 新对话，直接生成并创建，不需要再 check
        conversation_id, title = await create_new_conversation(current_user_id, user_content, agent_name)
    else:
        # 情况 B: 传入了 ID，必须检查是否存在且属于该用户
        await check_conversation_availability(conversation_id, current_user_id)

    # 2. 业务逻辑：将当前用户消息先存入数据库
    content = [{"type": "text", "text": user_content}]
    # 如果有附件，进行分类处理
    if file_attachments:
        for url in file_attachments:
            # --- 解析 URL 获取文件名和后缀 ---
            parsed_url = urlparse(url)
            # 获取路径部分，例如 /path/to/image.jpg
            path = parsed_url.path
            # 提取文件名，例如 image.jpg
            file_name = os.path.basename(path) or "unknown_file"
            # 提取后缀并转为小写，例如 .jpg
            _, ext = os.path.splitext(file_name)
            ext = ext.lower().strip('.')
            # --- 分类处理 ---
            # 定义常见的图片后缀
            image_extensions = ["jpg", "jpeg", "png", "webp", "gif", "bmp"]
            if ext in image_extensions:
                # A. 如果是图片，按多模态格式存储
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": url,
                        "detail": "auto"
                    }
                })
                # 辅助文本，告知模型图片名称
                content.append({"type": "text", "text": json.dumps({"file_attachment": url, "correspond_to": "image above"}, ensure_ascii=False) })
            else:
                # B. 如果是文档或其他文件
                content.append({"type": "text", "text": json.dumps({"file_attachment": url}, ensure_ascii=False) })

    # 序列化为字符串存储
    content_string = json.dumps(content, ensure_ascii=False)

    await save_message_to_db(conversation_id, "user", content_string)

    # 3. 获取历史上下文（从数据库）
    history_messages = await get_history_from_db(conversation_id)
    # print("历史会话记录：")
    # print(history_messages)

    # 4. 定义异步生成器处理流式逻辑
    async def event_generator():
        try:
            # ⭐ 获取限流槽位（按agent分别限流）
            await acquire_slot(conversation_id, agent_name)
            
            # 对于创建新会话，告诉前端新会话的id
            if not request.conversation_id:
                yield f"data: {json.dumps({'type': 'config', 'conversation_id': conversation_id, 'title':title})}\n\n"

            # 这两个变量用于临时存储当前这一轮 assistant 回复的完整内容
            current_assistant_content = ""
            current_assistant_tool_calls = None
            
            async for msg_dict in agent_class(history_messages, conversation_id, current_user_id):

                # 📊 处理usage消息（内部消息，不转发给前端）
                if msg_dict.get("role") == "usage":
                    continue  # 不转发给前端
                
                # 💓 处理心跳消息（工具执行期间保活 SSE 连接，不转发业务数据给前端）
                if msg_dict.get("role") == "heartbeat":
                    yield ": heartbeat\n\n"
                    continue
                
                # 1. 第一时间把数据推给前端，保证 stream 的实时性
                yield f"data: {json.dumps(msg_dict, ensure_ascii=False)}\n\n"

                role = msg_dict.get("role")

                if role == "assistant":
                    # 累加文本碎片
                    if "content" in msg_dict and msg_dict["content"]:
                        current_assistant_content += msg_dict["content"]
                    
                    # 记录工具调用（如果有）
                    if "tool_calls" in msg_dict:
                        current_assistant_tool_calls = msg_dict["tool_calls"]
                    
                    # 关键：什么时候存 assistant 消息？
                    # 如果 material_assistant 在输出完一个 assistant 段落后会发出信号，
                    # 或者进入到下一个角色（tool），就说明这一段 assistant 结束了。
                    # 这里简单处理：在循环结束处统一存。

                elif role == "tool":
                    # 既然出现了 role == "tool"，说明之前的 assistant 肯定结束了

                    tool_content = msg_dict.get("content")
                    tool_call_id = msg_dict.get("tool_call_id")

                    # 保存之前的 assistant 消息并清空，准备接下一轮
                    if current_assistant_content or current_assistant_tool_calls:
                        await save_message_to_db(
                            conversation_id,
                            "assistant",
                            content=current_assistant_content,
                            tool_calls=json.dumps(current_assistant_tool_calls) if current_assistant_tool_calls else None
                        )
                        current_assistant_content = ""
                        current_assistant_tool_calls = None

                    # 存储当前的工具执行结果（tool 消息通常不是流式的，直接存）
                    await save_message_to_db(
                        conversation_id, 
                        "tool", 
                        content=tool_content, 
                        tool_call_id=tool_call_id
                    )

            # 5. 循环彻底结束（可能 AI 最后说了一句话作为总结）
            if current_assistant_content or current_assistant_tool_calls:
                await save_message_to_db(
                    conversation_id, 
                    "assistant", 
                    content=current_assistant_content, 
                    tool_calls=json.dumps(current_assistant_tool_calls) if current_assistant_tool_calls else None
                )
            
            yield "data: [DONE]\n\n"

        except Exception as e:
            print(f"❌ 异常发生: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            yield "data: [DONE]\n\n"
        finally:
            print(f"🔴 调用 finally 块，开始清理对话 {conversation_id}")
            # ⭐ 释放限流槽位
            try:
                await release_slot(conversation_id, agent_name)
            except Exception as e:
                print(f"⚠️ 释放槽位失败: {e}")
            print(f"✅ finally 块清理完成")
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")


class StopGenerationRequest(BaseModel):
    conversation_id: str

@router.post("/stop_generation")
async def stop_generation(
    request: StopGenerationRequest,
    current_user_id: Annotated[str, Depends(get_current_user)]
):
    conversation_id = request.conversation_id
    await check_conversation_availability(conversation_id, current_user_id)
    
    # 使用导入的函数
    await set_stop_flag(conversation_id, True)
    
    return {"status": "ok", "message": f"对话 {conversation_id} 已停止"}


#------------------------------------------------------------------------------

class query_conversations_class(BaseModel):
    agent_name: str

# user_id 对应的所有 conversation 记录查询
@router.post("/query_conversations")
async def query_conversations(
    request : query_conversations_class,
    current_user_id: Annotated[str, Depends(get_current_user)]
):
    agent_name = request.agent_name
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row 
        # 使用参数化查询防止 SQL 注入
        query = '''
            SELECT conversation_id, user_id, title, agent_name, is_pinned, create_time, update_time 
            FROM conversations 
            WHERE user_id = ? AND agent_name = ?
            ORDER BY is_pinned DESC, update_time DESC
        '''
        try:
            cursor = await conn.execute(query, (current_user_id, agent_name))
            rows = await cursor.fetchall()
            # 利用 aiosqlite.Row 的特性，直接将 row 转为 dict
            # 这样即使你 SELECT 的字段顺序变了，代码也不会崩
            conversations = [dict(row) for row in rows]
            # 如果需要对某些字段做特殊处理（如布尔转换）
            for conv in conversations:
                conv["is_pinned"] = bool(conv["is_pinned"])
            return {"status": "success", "data": conversations}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


class query_messages_in_conversation_class(BaseModel):
    conversation_id: str

# 指定 conversation_id，返回 messages 表中全部相关记录
@router.post("/query_messages_in_conversation")
async def query_messages_in_conversation(
    request: query_messages_in_conversation_class,
    current_user_id: Annotated[str, Depends(get_current_user)]
):
    conversation_id = request.conversation_id
    async with aiosqlite.connect(DB_PATH) as conn:
        try:
            conn.row_factory = aiosqlite.Row # 使结果可以按列名访问
            # 安全校验：先确认该 conversation 是否属于当前用户，防止越权访问
            cursor = await conn.execute('SELECT user_id FROM conversations WHERE conversation_id = ?', (conversation_id,))
            conv = await cursor.fetchone()
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
            if conv[0] != current_user_id:
                raise HTTPException(status_code=403, detail="Permission denied")
            # 按创建时间升序排列，以还原聊天顺序
            cursor = await conn.execute('''
                SELECT message_id, role, content, tool_calls, tool_call_id, tokens_used, create_time 
                FROM messages 
                WHERE conversation_id = ? 
                ORDER BY create_time ASC
            ''', (conversation_id,))
            rows = await cursor.fetchall()
            messages = [
                {
                    "message_id": row[0],
                    "role": row[1],
                    "content": row[2],
                    "tool_calls": row[3], # 存储时是 JSON 字符串
                    "tool_call_id": row[4],
                    "tokens_used": row[5],
                    "create_time": row[6]
                } for row in rows
            ]
            return {"status": "success", "conversation_id": conversation_id, "data": messages}
        except HTTPException as he:
            raise he
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")



class delete_conversation_class(BaseModel):
    conversation_id: str

# 删除指定 conversation 及其关联的所有 messages
@router.post("/delete_conversation")
async def delete_conversation(
    request: delete_conversation_class,
    current_user_id: Annotated[str, Depends(get_current_user)]
):
    conversation_id = request.conversation_id
    async with aiosqlite.connect(DB_PATH) as conn:
        try:
            # 1. 安全校验：确保该会话属于当前用户，防止越权删除
            cursor = await conn.execute('SELECT user_id FROM conversations WHERE conversation_id = ?', (conversation_id,))
            conv = await cursor.fetchone()
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
            if conv[0] != current_user_id:
                raise HTTPException(status_code=403, detail="Permission denied")
            # 2. 执行硬删除
            # 如果你的 SQLite 数据库没有配置外键级联删除 (ON DELETE CASCADE)，则需要手动删除两个表
            await conn.execute('DELETE FROM messages WHERE conversation_id = ?', (conversation_id,))
            await conn.execute('DELETE FROM conversations WHERE conversation_id = ?', (conversation_id,))
            await conn.commit()
            return {"status": "success", "message": f"Conversation {conversation_id} deleted successfully"}
        except HTTPException as he:
            raise he
        except Exception as e:
            if 'conn' in locals():
                conn.rollback() # 出错时回滚
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/query_agent_info")
async def query_agent_info(language: str = "en", agent_name: str = None):
    """
    从 profiles.yaml 配置获取 agent 信息
    
    Args:
        language: "zh" 或 "en"，默认 "en"
        agent_name: 可选，指定查询特定 agent
    """
    try:
        yaml_manager = await get_profiles_manager()
        all_profiles = await yaml_manager.read()
        
        # 确定语言
        lang_key = "zh" if language == "zh" else "en"
        
        # 构建返回数据
        agents_list = []
        for name, profile in all_profiles.items():
            # 跳过未激活的 agent
            if not profile.get("is_active", True):
                continue
            
            # 如果指定了 agent_name，只返回该 agent（优先级最高，不受 is_standalone 限制）
            if agent_name:
                if name != agent_name:
                    continue
                # name == agent_name，继续处理这个 agent
            else:
                # 没有指定 agent_name 时，过滤掉 is_standalone 的 agent（独立入口）
                if profile.get("is_standalone", False):
                    continue
            
            lang_data = profile.get(lang_key, {})
            agent_dict = {
                "name": name,
                "avatar": profile.get("avatar"),
                "type": lang_data.get("type"),
                "nick_name": lang_data.get("nick_name"),
                "description": lang_data.get("description"),
                "tags": lang_data.get("tags_zh" if lang_key == "zh" else "tags_en", []),
                "starter_prompts": lang_data.get("starter_prompts_zh" if lang_key == "zh" else "starter_prompts_en", []),
            }
            agents_list.append(agent_dict)
        
        return {
            "success": True,
            "data": agents_list
        }
    except Exception as e:
        print(f"读取 profiles.yaml 失败: {e}")
        raise HTTPException(status_code=500, detail=f"配置读取异常: {str(e)}")
    


class AgentCreateSchema(BaseModel):
    name: str = Field(..., example="research_expert")
    avatar: str | None = None
    type_zh: str
    nick_name_zh: str
    description_zh: str
    tags_zh: list[str] = []
    starter_prompts_zh: list[str] = []
    type_en: str
    nick_name_en: str
    description_en: str
    tags_en: list[str] = []
    starter_prompts_en: list[str] = []
    is_active: int = 1

# 创建/更新接口
@router.post("/create_edit_agent")
async def create_edit_agent(
    agent: AgentCreateSchema, 
    current_admin_id: Annotated[str, Depends(get_current_admin)]
):
    """
    管理员接口：创建或更新 Agent 配置到 profiles.yaml
    """
    try:
        yaml_manager = await get_profiles_manager()
        
        # 构建要写入的数据结构
        agent_config = {
            "avatar": agent.avatar,
            "zh": {
                "type": agent.type_zh,
                "nick_name": agent.nick_name_zh,
                "description": agent.description_zh,
                "tags_zh": agent.tags_zh,
                "starter_prompts_zh": agent.starter_prompts_zh,
            },
            "en": {
                "type": agent.type_en,
                "nick_name": agent.nick_name_en,
                "description": agent.description_en,
                "tags_en": agent.tags_en,
                "starter_prompts_en": agent.starter_prompts_en,
            },
            "is_active": bool(agent.is_active)
        }
        
        # 更新到 YAML
        await yaml_manager.update_agent(agent.name, agent_config)
        
        return {
            "success": True, 
            "message": f"Agent '{agent.name}' 已成功创建/更新到 profiles.yaml"
        }
    except Exception as e:
        print(f"更新 profiles.yaml 失败: {e}")
        raise HTTPException(status_code=500, detail=f"配置更新失败: {str(e)}")