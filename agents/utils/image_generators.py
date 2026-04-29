"""
各大模型厂商的生图（文本到图像）函数定义
支持多个生图API的统一接口
"""

import asyncio
from typing import Dict, Any, Optional, List


#----------------------------------------------------------------------------------------
# Doubao (豆包) - 字节跳动
#----------------------------------------------------------------------------------------

async def generate_image_doubao(
    model_config: Dict[str, Any],
    prompt: str,
    size: str = "2K",
    response_format: str = "url",
    watermark: bool = True,
    images: Optional[List[str]] = None,
    sequential_image_generation: str = "disabled",
    extra_body: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    使用豆包（Doubao）模型生成图片，支持文生图和图生图两种模式
    
    参数:
    - model_config: 模型配置字典，包含：
      * base_url: API服务地址
      * api_key: API密钥
      * model: 模型名称（如"doubao-seedream-5-0-260128"）
    - prompt: 生图提示词（详细的图片描述）
    - size: 生成图片尺寸，支持 "2K"、"1K" 等（默认 "2K"）
    - response_format: 响应格式，"url" 或 "b64_json"（默认 "url"）
    - watermark: 是否添加水印（默认 True）
    - images: 输入图片URL列表（用于图生图模式，不提供时为文生图模式）
    - sequential_image_generation: 顺序生成开关（"enabled" 或 "disabled"，默认 "disabled"）
    - extra_body: 额外的API参数（会合并到请求中）
    
    返回:
    {
        "result": "success|failure",
        "image_url": "生成的图片URL（如果成功）",
        "file_attachment": "图片URL（用于前端展示）",
        "message": "错误信息或成功信息",
        "mode": "text-to-image|image-to-image",
        "raw_response": "原始API响应（用于调试）"
    }
    """
    
    try:
        # 导入OpenAI客户端（豆包使用OpenAI兼容API）
        from openai import AsyncOpenAI
        
        # 验证必要的配置
        base_url = model_config.get("base_url")
        api_key = model_config.get("api_key")
        model = model_config.get("model")
        
        if not all([base_url, api_key, model]):
            return {
                "result": "failure",
                "message": "模型配置不完整，需要提供 base_url、api_key 和 model"
            }
        
        if not prompt or not prompt.strip():
            return {
                "result": "failure",
                "message": "生图提示词不能为空"
            }
        
        # 初始化异步OpenAI客户端
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key
        )
        
        # 确定工作模式（文生图 vs 图生图）
        mode = "text-to-image"
        
        # 构建请求参数
        request_params = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "response_format": response_format,
        }
        
        # 初始化 extra_body
        extra_body_params = {
            "watermark": watermark,
            "sequential_image_generation": sequential_image_generation
        }
        
        # 如果提供了输入图片，使用图生图模式
        if images and len(images) > 0:
            mode = "image-to-image"
            extra_body_params["image"] = images
        
        # 合并额外参数
        if extra_body:
            extra_body_params.update(extra_body)
        
        request_params["extra_body"] = extra_body_params
        
        # 调用API生成图片
        images_response = await client.images.generate(**request_params)
        
        # 提取图片URL
        if images_response.data and len(images_response.data) > 0:
            image_url = images_response.data[0].url
            
            return {
                "result": "success",
                "image_url": image_url,
                "file_attachment": image_url,  # 前端用于展示/下载
                "mode": mode,
                "message": f"图片生成成功（模式：{mode}）"
            }
        else:
            return {
                "result": "failure",
                "message": "API返回了空的图片数据",
                "raw_response": str(images_response)
            }
    
    except Exception as e:
        return {
            "result": "failure",
            "message": f"生图过程中发生错误: {str(e)}",
            "error_type": type(e).__name__
        }


#----------------------------------------------------------------------------------------
# 生图工厂函数 - 根据模型类型自动选择对应的生图函数
#----------------------------------------------------------------------------------------

async def generate_image(
    model_config: Dict[str, Any],
    prompt: str,
    generator_type: str = "auto",
    images: Optional[List[str]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    统一的生图接口，支持文生图和图生图模式，自动根据模型类型选择对应的生图函数
    
    参数:
    - model_config: 模型配置字典，包含 base_url、api_key、model 等
    - prompt: 生图提示词（文生图时为图片描述，图生图时为修改指令）
    - generator_type: 生图器类型（"auto"、"doubao" 等）
    - images: 输入图片URL列表（不提供时为文生图模式，提供时为图生图模式）
    - **kwargs: 其他生图参数（size、response_format、watermark 等）
    
    返回: 统一格式的生图结果
    """
    
    # 自动识别生图器类型
    if generator_type == "auto":
        model_name = model_config.get("model", "").lower()
        
        if "doubao" in model_name or "seedream" in model_name:
            generator_type = "doubao"
        else:
            return {
                "result": "failure",
                "message": f"无法识别模型类型: {model_name}"
            }
    
    # 根据类型调用对应的生图函数
    if generator_type == "doubao":
        return await generate_image_doubao(
            model_config,
            prompt,
            images=images,
            **kwargs
        )
    else:
        return {
            "result": "failure",
            "message": f"不支持的生图器类型: {generator_type}"
        }