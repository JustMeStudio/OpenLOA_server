import os
import io
import json
import time
import inspect
import asyncio
import httpx
import tiktoken
import pandas as pd
import fitz  # PyMuPDF
from docx import Document
from openai import AsyncOpenAI
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, quote
from agents.utils.config import load_user_settings
from agents.globals.context import PENDING_MESSAGES, MESSAGE_ADDED_EVENT
from utils.generation_manager import is_stopped

load_dotenv()

# --- 全局共享 AsyncOpenAI 客户端池（按 base_url+api_key 缓存，连接池复用）---
_openai_client_cache: dict[tuple, AsyncOpenAI] = {}

def _get_openai_client(api_key: str, base_url: str) -> AsyncOpenAI:
    """
    获取全局缓存的 AsyncOpenAI 客户端（单例模式）。
    同一个 base_url + api_key 组合复用同一个实例，避免每次创建新实例时
    openai SDK 在 GC 时关闭底层 httpx 连接池。
    """
    key = (base_url, api_key)
    if key not in _openai_client_cache:
        _openai_client_cache[key] = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=10.0,   # 握手10s足够，再长说明网络真断了
                read=600.0,     # 给足10分钟！应对极长Thinking、深度搜索或几万字的非流式返回
                write=60.0,    # 上传超长Context（如长文档）时，给足发送时间
                pool=None       # 设为 None，表示只要连接池有位置，等多久都行，不报池超时错误
            )
        )
    return _openai_client_cache[key]


def _estimate_tokens_for_messages(messages: list, model: str = "gpt-4") -> int:
    """
    估算消息列表对应的 token 数
    用于中断情况下的 fallback token 计算
    """
    if tiktoken is None:
        # fallback：粗略估计 1 token ≈ 4 字符
        text = ""
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if content:
                    text += str(content)
        return max(1, len(text) // 4)
    
    try:
        enc = tiktoken.encoding_for_model(model)
        text = ""
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if content:
                    text += str(content)
        return len(enc.encode(text))
    except Exception as e:
        print(f"⚠️ Token 估算失败: {e}")
        # fallback
        text = ""
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if content:
                    text += str(content)
        return max(1, len(text) // 4)


def _estimate_tokens_for_text(text: str, model: str = "gpt-4") -> int:
    """
    估算文本对应的 token 数
    """
    if tiktoken is None:
        # fallback
        return max(1, len(text) // 4)
    
    try:
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    except Exception as e:
        print(f"⚠️ Token 估算失败: {e}")
        return max(1, len(text) // 4)


# asynchronous chat function that supports tool calls and dyLucyc response handling
async def chat(model, system_prompt: str = "", messages=[], 
               tools=[], tool_registry={}, 
               conversation_id: str = None, user_id: str = None,
               enable_thinking=True, 
               enable_search=True, forced_search=False, search_strategy="turbo",
               explicit_cache=True):
    # 初始化：清空消息队列
    PENDING_MESSAGES.set([])
    MESSAGE_ADDED_EVENT.set(asyncio.Event())
    
    # 使用全局缓存的 AsyncOpenAI 单例，连接池长期复用，避免冷启动和 GC 关闭连接池
    client = _get_openai_client(model["api_key"], model["base_url"])

    # 系统提示作为第一条消息
    system_message = {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": system_prompt
            }
        ]
    }

    # 拼接上下文
    full_messages = [system_message] + messages

    # 如果开启了显式缓存，在full_messages中最后一个消息对象的content里的最后一个元素添加cache_control字段
    if explicit_cache and full_messages:
        # 首先对system_message插入标记
        full_messages[0]["content"][-1]["cache_control"] = {"type": "ephemeral"}
        # 然后标记最后一条消息
        last_msg = full_messages[-1]
        if isinstance(last_msg.get("content"), list):
            last_msg["content"][-1]["cache_control"] = {"type": "ephemeral"}
        elif isinstance(last_msg.get("content"), str):
            last_msg["content"] = [
                {
                    "type": "text",
                    "text": last_msg["content"],
                    "cache_control": {"type": "ephemeral"}
                }
            ]
    
    # 初始化token统计
    accumulated_tokens = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0
    }

    while True:
        # 计算当前上下文的预估 prompt token（用于中断时的 fallback）
        estimated_prompt_tokens = _estimate_tokens_for_messages(full_messages)

        try:
            response = await client.chat.completions.create(
                model=model["model"],
                messages=full_messages,
                **({"tools": tools} if tools else {}),
                stream=True,
                stream_options={"include_usage": True},
                temperature=1.0,
                extra_body={
                    "enable_thinking": enable_thinking,
                    "enable_search": enable_search,
                    # 可选：配置搜索策略
                    "search_options": {
                        "forced_search": forced_search,
                        "search_strategy": search_strategy  # 或 "max", "agent", "agent_max"
                    }
                }
            )
        except Exception as e:
            raise
        collected_content = ""
        tool_calls_map = {}
        current_turn_tokens = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }
        should_break = False

        try:
            async for chunk in response:
                # 检查停止标志（每个chunk开始时检查）
                try:
                    is_stopped_result = await is_stopped(conversation_id)
                except Exception as e:
                    raise
                
                if is_stopped_result:
                    # 中断时，估算并 yield token 统计
                    estimated_completion_tokens = _estimate_tokens_for_text(collected_content)

                    yield {
                        "role": "usage",
                        "usage": {
                            "prompt_tokens": estimated_prompt_tokens,
                            "completion_tokens": estimated_completion_tokens,
                            "total_tokens": estimated_prompt_tokens + estimated_completion_tokens
                        }
                    }
                    should_break = True
                    break

                # 跳过 choices 为空的 chunk
                if not chunk.choices:
                    # 仍然收集 usage 信息
                    if hasattr(chunk, 'usage') and chunk.usage:
                        current_turn_tokens["prompt_tokens"] = chunk.usage.prompt_tokens or 0
                        current_turn_tokens["completion_tokens"] = chunk.usage.completion_tokens or 0
                        current_turn_tokens["total_tokens"] = chunk.usage.total_tokens or 0
                    continue

                delta = chunk.choices[0].delta
                # 处理文本碎片
                if delta.content:
                    content_piece = delta.content
                    collected_content += content_piece
                    yield {"role": "assistant", "content": content_piece}
                # 处理工具调用碎片
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        if tc.index not in tool_calls_map:
                            tool_calls_map[tc.index] = {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": "", "arguments": ""}
                            }
                        if tc.function.name:
                            tool_calls_map[tc.index]["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_map[tc.index]["function"]["arguments"] += tc.function.arguments

        except asyncio.CancelledError:
            # 如果没有主动停止，则在被取消时 yield token
            if not should_break:
                estimated_completion_tokens = _estimate_tokens_for_text(collected_content)
                yield {
                    "role": "usage",
                    "usage": {
                        "prompt_tokens": estimated_prompt_tokens,
                        "completion_tokens": estimated_completion_tokens,
                        "total_tokens": estimated_prompt_tokens + estimated_completion_tokens
                    }
                }
                should_break = True
            # 不要 raise，优雅处理
        except Exception as e:
            # 不要 raise，让后续清理逻辑有机会执行
            pass
        # 累加本轮token
        if current_turn_tokens["total_tokens"] > 0:
            accumulated_tokens["prompt_tokens"] += current_turn_tokens["prompt_tokens"]
            accumulated_tokens["completion_tokens"] += current_turn_tokens["completion_tokens"]
            accumulated_tokens["total_tokens"] += current_turn_tokens["total_tokens"]

        # 流式结束后的逻辑处理
        # 如果已经因为停止信号 yield 过 token，则跳过工具和最后的 yield
        if should_break:
            break
        
        if tool_calls_map:
            formatted_tool_calls = [v for k, v in sorted(tool_calls_map.items())]
            # 1. 记录 assistant 的工具请求到上下文
            assistant_msg = {
                "role": "assistant",
                "content": collected_content or None,
                "tool_calls": formatted_tool_calls
            }
            full_messages.append(assistant_msg)
            # 2. 通知外层存入数据库
            yield {"role": "assistant", "tool_calls": formatted_tool_calls}
            
            # 3. 执行工具逻辑
            for tc_data in formatted_tool_calls:
                t_name = tc_data["function"]["name"]
                t_args = json.loads(tc_data["function"]["arguments"])
                
                seen_message_count = 0
                message_queue_holder = {'messages': []}
                result = None
                
                async def background_queue_monitor():
                    """监听消息队列并收集新消息"""
                    nonlocal seen_message_count
                    event = MESSAGE_ADDED_EVENT.get()
                    
                    while True:
                        try:
                            current_messages = PENDING_MESSAGES.get()
                            if current_messages and len(current_messages) > seen_message_count:
                                # 有新消息收集
                                new_msgs = current_messages[seen_message_count:]
                                message_queue_holder['messages'].extend(new_msgs)
                                seen_message_count = len(current_messages)
                            else:
                                # 没有新消息，等待事件信号 (最长 50ms)
                                try:
                                    await asyncio.wait_for(event.wait(), timeout=0.05)
                                except asyncio.TimeoutError:
                                    pass
                                finally:
                                    if event:
                                        event.clear()
                        except asyncio.CancelledError:
                            break
                        except Exception:
                            await asyncio.sleep(0.01)
                
                # 自动注入 server-side 上下文参数（不暴露给模型）
                _sig = inspect.signature(tool_registry[t_name])
                if 'user_id' in _sig.parameters:
                    t_args['user_id'] = user_id
                if 'conversation_id' in _sig.parameters:
                    t_args['conversation_id'] = conversation_id

                queue_monitor_task = asyncio.create_task(background_queue_monitor())
                tool_result_task = asyncio.create_task(tool_registry[t_name](**t_args))
                
                try:
                    _last_heartbeat = time.monotonic()
                    while not tool_result_task.done():
                        if message_queue_holder['messages']:
                            pending_msg = message_queue_holder['messages'].pop(0)
                            yield pending_msg
                            _last_heartbeat = time.monotonic()
                        else:
                            # 工具执行期间定期发送心跳，防止 SSE 连接因长时间无数据而超时断开
                            _now = time.monotonic()
                            if _now - _last_heartbeat >= 10:
                                yield {"role": "heartbeat"}
                                _last_heartbeat = _now
                        
                        await asyncio.sleep(0.02)
                    
                    # 任务已完成，获取结果
                    try:
                        result = await tool_result_task
                    except Exception as e:
                        result = {"result": "failed", "error": str(e)}
                    
                finally:
                    queue_monitor_task.cancel()
                    try:
                        await queue_monitor_task
                    except asyncio.CancelledError:
                        pass
                    
                    while message_queue_holder['messages']:
                        remaining_msg = message_queue_holder['messages'].pop(0)
                        yield remaining_msg
                    
                    try:
                        current_messages = PENDING_MESSAGES.get()
                        if current_messages and len(current_messages) > seen_message_count:
                            final_leftover = current_messages[seen_message_count:]
                            for final_msg in final_leftover:
                                yield final_msg
                    except Exception:
                        pass
                
                
                # 序列化结果
                content = json.dumps(result, ensure_ascii=False)
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc_data["id"],
                    "content": content
                }
                full_messages.append(tool_msg)
                # Yield 工具执行结果给前端和存库逻辑
                yield tool_msg
            # 执行完工具后，while True 会继续下一轮循环，让模型根据工具结果说话
        else:
            # 对话结束
            full_messages.append({"role": "assistant", "content": collected_content})
            PENDING_MESSAGES.set([])
            
            # 对话完全结束，发送token统计信息
            if not should_break:
                yield {
                    "role": "usage",
                    "usage": {
                        "prompt_tokens": accumulated_tokens["prompt_tokens"],
                        "completion_tokens": accumulated_tokens["completion_tokens"],
                        "total_tokens": accumulated_tokens["total_tokens"]
                    }
                }
            break


# get text response from LLM API, with retry mechanism for network issues
async def request_LLM_api(model_config: dict, prompt: str, system_prompt: str = "", 
                          response_format=None, 
                          enable_thinking=True, 
                          enable_search=True, forced_search=False, search_strategy="turbo",
                          explicit_cache=True):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]
    
    # 如果开启了显式缓存，在消息的 content 中添加 cache_control 字段
    if explicit_cache and messages:
        # 只对 system message 添加 cache_control（user message 每次都不同，不缓存）
        messages[0]["content"] = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}
            }
        ]
    
    client = _get_openai_client(model_config["api_key"], model_config["base_url"])
    for i in range(3):
        try:
            print(f"[API] Requesting {model_config['model']}...")
            completion = await client.chat.completions.create(
                model=model_config["model"],
                messages=messages,
                response_format=response_format,
                temperature=1.0,
                extra_body={
                    "enable_thinking": enable_thinking,
                    "enable_search": enable_search,
                    "search_options": {
                        "forced_search": forced_search,
                        "search_strategy": search_strategy
                    }
                }
            )
            content = completion.choices[0].message.content
            return content
        except Exception as e:
            print(f"[Retry] Attempt {i+1}/3 - Error: {str(e)}")
            if i < 2:
                continue
    # after 3 failed attempts, return None
    print("[Error] LLM API request failed after 3 attempts")
    return None


# read content from url file
async def read_file_from_url(url: str, is_binary: bool = False):
    """
    通用多格式文件读取工具函数
    支持: PDF, DOCX, XLSX, CSV, TXT, MD, 以及任意二进制文件
    
    参数:
    - url: 文件URL
    - is_binary: 是否以二进制模式读取（用于图片、音频等二进制文件）
    """
    try:
        # 0. 对URL进行编码处理（处理中文等特殊字符）
        # 解析URL并对路径部分进行编码
        parsed = urlparse(url)
        # 对路径进行编码，safe='/' 保留路径分隔符
        encoded_path = quote(parsed.path, safe='/')
        # 重建编码后的URL（保留query string）
        if parsed.query:
            encoded_url = f"{parsed.scheme}://{parsed.netloc}{encoded_path}?{parsed.query}"
        else:
            encoded_url = f"{parsed.scheme}://{parsed.netloc}{encoded_path}"
        
        # 1. 异步下载文件内容 (使用 stream 模式更安全)
        # 添加标准浏览器请求头以解决 CDN 内容协商问题，同时保留后端识别头
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",  # 接受所有格式
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "X-Source-ID": "internal"
        }
        
        # 首先尝试启用SSL验证，如果失败则禁用验证重试
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, verify=True) as client:
                response = await client.get(encoded_url, headers=headers)
                if response.status_code != 200:
                    return {"result": "error", "message": f"下载失败，状态码：{response.status_code}"}
                content_bytes = response.content
                # 获取文件名或后缀进行格式判断
                file_ext = os.path.splitext(url.split('?')[0])[-1].lower()
        except Exception as ssl_error:
            # SSL证书验证失败，使用禁用验证重试
            print(f"[Warning] SSL验证失败，尝试禁用验证重试: {str(ssl_error)}")
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, verify=False) as client:
                response = await client.get(encoded_url, headers=headers)
                if response.status_code != 200:
                    return {"result": "error", "message": f"下载失败，状态码：{response.status_code}"}
                content_bytes = response.content
                # 获取文件名或后缀进行格式判断
                file_ext = os.path.splitext(url.split('?')[0])[-1].lower()
    except Exception as e:
        return {"result": "error", "message": f"网络请求异常: {str(e)}"}
    
    # 如果请求二进制模式，直接返回原始字节数据
    if is_binary:
        return content_bytes
    
    try:
        # 2. 根据后缀名进行分格式解析
        # --- PDF 格式 (使用 PyMuPDF/fitz) ---
        if file_ext == '.pdf':
            text = ""
            with fitz.open(stream=content_bytes, filetype="pdf") as doc:
                for page in doc:
                    text += page.get_text()
            return text.strip()
        # --- Word 格式 (使用 python-docx) ---
        elif file_ext in ['.docx', '.doc']:
            # 注意：python-docx 不原生支持旧版 .doc，通常需转为 .docx
            f = io.BytesIO(content_bytes)
            doc = Document(f)
            return "\n".join([para.text for para in doc.paragraphs]).strip()
        # --- Excel 格式 (使用 pandas) ---
        elif file_ext in ['.xlsx', '.xls']:
            f = io.BytesIO(content_bytes)
            df = pd.read_excel(f)
            # 将表格转换为字符串描述，适合 AI 处理
            return df.to_string(index=False)
        # --- CSV 格式 ---
        elif file_ext == '.csv':
            f = io.BytesIO(content_bytes)
            # 尝试 utf-8，失败则尝试 gbk
            try:
                df = pd.read_csv(f, encoding='utf-8')
            except:
                f.seek(0)
                df = pd.read_csv(f, encoding='gbk')
            return df.to_string(index=False)
        # --- 文本/Markdown 格式 ---
        elif file_ext in ['.txt', '.md', '.html']:
            # 自动处理编码
            try:
                return content_bytes.decode('utf-8')
            except UnicodeDecodeError:
                return content_bytes.decode('gbk')
        else:
            # 如果没有匹配到后缀，尝试直接当作文本读取
            try:
                return content_bytes.decode('utf-8')
            except:
                return {"result": "error", "message": f"暂不支持解析该文件格式: {file_ext}"}
    except Exception as e:
        return {"result": "error", "message": f"文件解析解析错误 ({file_ext}): {str(e)}"}