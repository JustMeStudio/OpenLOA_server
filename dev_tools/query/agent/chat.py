import asyncio
import httpx
import json

async def chat_with_agent_example():
    # 你的后端地址
    url = "http://127.0.0.1:9000/agent/chat"
    
    # 模拟认证 Token (对应 Depends(get_current_user))
    headers = {
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleGFtcGxlIjoiZXhhbXBsZSJ9.example",
        "Content-Type": "application/json"
    }
    
    # 请求体
    payload = {
        "agent_name": "Lucy", # 必须在你的 AGENT_MAP 中存在
        "content": "帮我查一下明天的天气，然后写一首关于天气的诗。",
        "conversation_id": None       # 第一次对话传 None
    }

    # 使用 httpx 的流式请求
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code != 200:
                print(f"Error: {response.status_code}")
                print(await response.aread())
                return

            print("--- 开始接收 AI 回复 ---")
            
            async for line in response.aiter_lines():
                # SSE 协议数据以 "data: " 开头
                if line.startswith("data: "):
                    data_str = line[6:] # 截掉前缀
                    
                    if data_str == "[DONE]":
                        print("\n--- 对话结束 ---")
                        break
                    
                    try:
                        data_json = json.loads(data_str)
                        
                        # 处理配置信息（如新生成的 conversation_id）
                        if data_json.get("type") == "config":
                            new_id = data_json.get("conversation_id")
                            print(f"[系统信息] 得到新对话 ID: {new_id}")
                        
                        # 处理 Assistant 回复内容
                        elif data_json.get("role") == "assistant":
                            content = data_json.get("content", "")
                            # 实时打印出文字，不换行
                            print(content, end="", flush=True)
                            
                        # 处理工具调用信息
                        elif data_json.get("role") == "tool":
                            print(f"\n[执行工具] 结果: {data_json.get('content')[:50]}...")

                    except json.JSONDecodeError:
                        continue

if __name__ == "__main__":
    asyncio.run(chat_with_agent_example())