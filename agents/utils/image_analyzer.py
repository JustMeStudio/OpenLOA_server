"""
图片理解工具模块 - 支持异步并发分析多张图片内容
使用 Qwen LVM 模型对图片进行内容描述
"""
import asyncio
from openai import AsyncOpenAI
from agents.utils.config import load_tool_config


# 加载图片理解模型配置
image_understander_model = load_tool_config("image_understander")


def _get_openai_client(api_key: str, base_url: str) -> AsyncOpenAI:
    """获取 AsyncOpenAI 客户端"""
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url
    )


async def image_analyzer(
    image_urls: list[str], 
    max_retries: int = 3,
    system_prompt: str | None = None,
    analysis_prompt_template: str | None = None,
    max_pixels: int = 250000
) -> dict:
    """
    异步并发分析多张图片内容
    
    Args:
        image_urls: 图片URL列表
        max_retries: 每张图片的重试次数
        system_prompt: 自定义系统提示词（可选，如果为None则使用默认提示词）
        analysis_prompt_template: 自定义分析提示词模板（可选，如果为None则使用默认提示词）
        max_pixels: LVM 模型处理图片的最大像素数，默认 250000
    
    Returns:
        {
            "result": "success" | "partial" | "failure",
            "total": 总数,
            "success": 成功数,
            "images": [
                {
                    "url": "原始URL",
                    "description": "图片内容描述",
                    "status": "success" | "failed",
                    "error": "错误信息（如果失败）"
                },
                ...
            ]
        }
    """
    # 使用默认提示词（如果未提供）
    if system_prompt is None:
        system_prompt = (
            "你是一位图片内容分析专家。你需要准确、详细地描述图片的视觉内容，"
            "帮助用户理解图片适合用于什么场景。"
        )
    
    if analysis_prompt_template is None:
        analysis_prompt_template = (
            "请详细描述这张图片的内容，包括：\n"
            "1. 图片中的主要对象和场景\n"
            "2. 图片的背景和环境\n"
            "3. 可见的文本或标志（如果有）\n"
            "4. 色彩和光线特点\n\n"
            "请用中文输出，简洁明了。"
        )
    
    if not image_urls:
        return {
            "result": "failure",
            "message": "没有图片URL",
            "total": 0,
            "success": 0,
            "images": []
        }
    
    # 并发处理所有图片
    tasks = [
        _understand_single_image(url, max_retries, system_prompt, analysis_prompt_template, max_pixels)
        for url in image_urls
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    
    # 检查是否所有图片都分析成功
    success_count = sum(1 for r in results if r.get("status") == "success")
    overall_status = "success" if success_count == len(results) else "partial"
    
    return {
        "result": overall_status,
        "total": len(results),
        "success": success_count,
        "images": list(results)
    }


async def _understand_single_image(
    image_url: str, 
    max_retries: int,
    system_prompt: str,
    analysis_prompt_template: str,
    max_pixels: int
) -> dict:
    """
    分析单张图片内容
    直接调用 LVM API 分析（由 API 服务器处理图片优化）
    """
    for attempt in range(max_retries):
        try:
            # 调用 LVM API
            description = await _analyze_image_with_lvm(image_url, system_prompt, analysis_prompt_template, max_pixels)
            
            if description:
                return {
                    "url": image_url,
                    "description": description,
                    "status": "success"
                }
            else:
                # LVM 返回 None，说明 API 调用失败
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))  # 递增延迟重试
                    continue
                return {
                    "url": image_url,
                    "description": None,
                    "status": "failed",
                    "error": "LVM API 分析失败"
                }
        
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))
                continue
            return {
                "url": image_url,
                "description": None,
                "status": "failed",
                "error": str(e)
            }
    
    return {
        "url": image_url,
        "description": None,
        "status": "failed",
        "error": "重试次数已用尽"
    }


async def _analyze_image_with_lvm(
    image_url: str,
    system_prompt: str,
    analysis_prompt_template: str,
    max_pixels: int
) -> str | None:
    """
    使用 Qwen LVM 模型分析图片内容
    直接传入图片 URL，由 API 服务器处理图片优化和压缩
    返回图片的详细描述，或 None 表示失败
    
    Args:
        image_url: 图片URL
        system_prompt: 系统提示词
        analysis_prompt_template: 分析提示词
        max_pixels: LVM 模型处理图片的最大像素数
    
    Returns:
        图片描述字符串，或 None 表示失败
    """
    if not image_understander_model:
        print("❌ 错误：未加载图片理解模型配置")
        return None
    
    client = _get_openai_client(
        image_understander_model["api_key"],
        image_understander_model["base_url"]
    )
    
    # 构建消息，包含系统提示词和图片URL
    messages = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"}  #显式创建缓存
                }
            ]
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": image_url}
                },
                {
                    "type": "text",
                    "text": analysis_prompt_template
                }
            ]
        }
    ]
    
    for attempt in range(3):
        try:
            print(f"[LVM] 分析图片 (尝试 {attempt + 1}/3)...")
            completion = await client.chat.completions.create(
                model=image_understander_model["model"],
                messages=messages,
                temperature=1.0,
                extra_body={
                    "vl_high_resolution_images": False,
                    "max_pixels": max_pixels,
                    "enable_thinking": False,
                    "enable_search": False
                }
            )
            description = completion.choices[0].message.content
            return description
        except Exception as e:
            print(f"[Retry] LVM API 调用失败 (尝试 {attempt + 1}/3): {str(e)}")
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)  # 指数退避
            else:
                return None