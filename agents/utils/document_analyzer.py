"""
通用文档分析模块 - Elsa 和 Bento 共用
"""
import json
from agents.utils.com import request_LLM_api, read_file_from_url
from urllib.parse import urlparse


async def analyze_document_generic(
    doc_urls: list[str],
    llm_model: str,
    system_prompt: str,
    analysis_prompt_template: str,
    result_fields: dict = None,
) -> dict:
    """
    通用文档分析函数，支持不同的 prompt 和返回字段结构。
    
    Args:
        doc_urls: 文档 URL 列表
        llm_model: LLM 模型配置
        system_prompt: 系统提示词
        analysis_prompt_template: 分析提示词模板（需包含 {combined_content} 占位符）
        result_fields: 返回字段定义，例如：
            {
                "summary": "摘要字段",
                "key_topics": "关键话题",
                "design_suggestions": "设计建议",  # 可选
                "image_themes": "图片主题"
            }
            默认会将这些字段添加到返回值中
    
    Returns:
        结构化分析结果，格式取决于 result_fields 定义
    """
    if not doc_urls:
        # 返回空结果，保持字段结构
        empty_result = {"result": "success"}
        if result_fields:
            for field in result_fields.keys():
                empty_result[field] = [] if field != "summary" else ""
        return empty_result
    
    try:
        # 1. 异步读取所有文档内容
        doc_contents = []
        for url in doc_urls:
            content = await read_file_from_url(url)
            if isinstance(content, dict) and content.get("result") == "error":
                print(f"⚠️ 文档读取失败: {url} - {content.get('message')}")
                continue
            doc_contents.append(content)
        
        if not doc_contents:
            return {
                "result": "failure",
                "message": "所有文档读取失败，无法分析",
                **{field: ([] if field != "summary" else "") for field in (result_fields or {}).keys()}
            }
        
        # 2. 合并文档内容并截断（防止过长）
        combined_content = "\n\n---\n\n".join(doc_contents)
        # 限制在8000字以内（约2000 tokens）
        if len(combined_content) > 8000:
            combined_content = combined_content[:8000] + "\n[内容过长，已截断...]"
        
        # 3. 填充分析 prompt
        analysis_prompt = analysis_prompt_template.format(combined_content=combined_content)
        
        # 4. 调用 LLM 进行分析
        analysis_result = await request_LLM_api(
            llm_model,
            analysis_prompt,
            system_prompt,
            response_format="json",
            enable_search=False,
            enable_thinking=False
        )
        
        # 5. 解析 JSON 结果
        try:
            # 尝试清理可能的 Markdown 包装
            cleaned_result = analysis_result.strip()
            if cleaned_result.startswith("```"):
                cleaned_result = cleaned_result.split("```")[1].lstrip("json\n")
            if cleaned_result.endswith("```"):
                cleaned_result = cleaned_result[:-3]
            
            parsed = json.loads(cleaned_result)
            
            # 构建返回结果
            return_value = {"result": "success"}
            if result_fields:
                for field_name in result_fields.keys():
                    return_value[field_name] = parsed.get(field_name, [] if field_name != "summary" else "")
            else:
                # 如果没有指定字段，返回所有解析结果
                return_value.update(parsed)
            
            return return_value
            
        except json.JSONDecodeError as e:
            print(f"⚠️ LLM 返回的内容解析失败: {str(e)}")
            print(f"原始返回: {analysis_result[:500]}")
            # 降级：返回简单格式
            result = {
                "result": "success",
                "summary": analysis_result[:200],
            }
            if result_fields:
                for field in list(result_fields.keys())[1:]:  # 除了 summary 之外的字段
                    result[field] = []
            return result
    
    except Exception as e:
        print(f"❌ 文档分析失败: {str(e)}")
        error_result = {
            "result": "failure",
            "message": f"文档分析失败: {str(e)}"
        }
        if result_fields:
            for field in result_fields.keys():
                error_result[field] = [] if field != "summary" else ""
        return error_result
