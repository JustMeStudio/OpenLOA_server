import os
import yaml
import json
import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Annotated
from fastapi import APIRouter, Depends
from pydantic import BaseModel

# 添加agents目录到路径，以便导入utils
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))
from agents.utils.com import request_LLM_api
from .security import get_current_user

router = APIRouter()

# Agent 使用教程路径
GUIDANCE_DIR = os.path.join(os.path.dirname(__file__), "..", "configs", "guidance")

# 加载公告配置
ANNOUNCEMENTS_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "configs", "announcements.yaml")

# 加载 profiles 配置
PROFILES_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "configs", "profiles.yaml")

# 加载 models 配置
MODELS_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "configs", "models.yaml")

def load_announcements() -> List[Dict[str, Any]]:
    """加载公告配置"""
    with open(ANNOUNCEMENTS_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("announcements", [])


def load_profiles() -> Dict[str, Any]:
    """加载 agent profiles 配置"""
    with open(PROFILES_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config if config else {}


def load_model_config_from_yaml(model_name: str = "agent_recommender") -> Dict[str, Any]:
    """从 models.yaml 中加载指定模型的配置"""
    with open(MODELS_CONFIG_PATH, "r", encoding="utf-8") as f:
        models = yaml.safe_load(f)
    return models.get(model_name, {})


def build_agent_descriptions(profiles: Dict[str, Any], language: str = "zh") -> str:
    """根据 profiles 构建 agent 描述文本，用于 LLM 分析
    
    Args:
        profiles: agent profiles 配置
        language: 语言选择 "zh" 或 "en"
    """
    descriptions = []
    for agent_name, agent_info in profiles.items():
        if agent_info.get("is_active") is False:
            continue
        
        if language == "zh":
            # 中文描述：使用中文昵称
            zh_info = agent_info.get("zh", {})
            agent_type = zh_info.get("type", "")
            nick_name = zh_info.get("nick_name", agent_name)
            description = zh_info.get("description", "")
            tags = zh_info.get("tags_zh", [])
            
            agent_desc = f"- {nick_name} ({agent_name}): 类型={agent_type}, 描述={description}"
            if tags:
                agent_desc += f", 标签={', '.join(tags)}"
        else:
            # 英文描述：使用英文信息
            en_info = agent_info.get("en", {})
            agent_type = en_info.get("type", "")
            nick_name = en_info.get("nick_name", agent_name)
            description = en_info.get("description", "")
            tags = en_info.get("tags_en", [])
            
            agent_desc = f"- {nick_name} ({agent_name}): Type={agent_type}, Description={description}"
            if tags:
                agent_desc += f", Tags={', '.join(tags)}"
        
        descriptions.append(agent_desc)
    
    return "\n".join(descriptions)


@router.get("/announcements")
async def get_announcements(
    language: str = "zh",
    type_filter: Optional[str] = None,
    status: Optional[str] = "active"
) -> Dict[str, Any]:
    """
    获取公告列表（支持筛选和多语言）
    Query Parameters:
        language: 语言选择，可选值 zh (中文) 或 en (英文)，默认zh
        type_filter: 可选，按公告类型筛选 (maintenance, important, feature, update)
        status: 可选，按状态筛选，默认只返回active状态的公告
    Returns:
        包含公告列表的响应对象，按优先级排序
    """
    try:
        # 验证语言参数
        if language not in ["zh", "en"]:
            language = "zh"
        
        announcements = load_announcements()
        # 按状态筛选
        if status:
            announcements = [a for a in announcements if a.get("status") == status]
        # 按类型筛选
        if type_filter:
            announcements = [a for a in announcements if a.get("type") == type_filter]
        # 按优先级排序（优先级低的在前）
        announcements = sorted(announcements, key=lambda x: x.get("priority", 10))
        
        # 处理多语言内容，提取指定语言的 title 和 message
        processed_announcements = []
        for announcement in announcements:
            processed = announcement.copy()
            # 如果 title 是字典，则提取指定语言；否则保持原样
            if isinstance(announcement.get("title"), dict):
                processed["title"] = announcement["title"].get(language, announcement["title"].get("zh", ""))
            # 如果 message 是字典，则提取指定语言；否则保持原样
            if isinstance(announcement.get("message"), dict):
                processed["message"] = announcement["message"].get(language, announcement["message"].get("zh", ""))
            processed_announcements.append(processed)
        
        return {
            "status": "success",
            "data": processed_announcements,
            "count": len(processed_announcements)
        }
    except Exception as e:
        return {
            "status": "error",
            "detail": f"Failed to load announcements: {str(e)}",
            "data": []
        }


@router.get("/guidance")
async def get_agent_guidance(agent_name: str, language: str = "zh") -> Dict[str, Any]:
    """
    获取指定agent的使用教程（markdown格式，支持多语言）
    Query Parameters:
        agent_name: agent的名称，对应 configs/guidance/{agent_name}.md 或 {agent_name}.{language}.md 文件
        language: 语言选择，可选值 zh (中文) 或 en (英文)，默认zh。优先查找对应语言的文件，找不到则降级到默认文件
    Returns:
        包含markdown格式使用教程的响应对象
    """
    try:
        # 构建文件路径（resolve() 会展开符号链接和相对路径）
        guidance_dir_path = Path(GUIDANCE_DIR).resolve()
        
        # 优先查找指定语言的文件，找不到则降级到默认文件
        guidance_file = (guidance_dir_path / f"{agent_name}.{language}.md").resolve()
        
        # 验证文件路径确实在指定目录内（防止目录遍历攻击和符号链接攻击）
        if not guidance_file.is_relative_to(guidance_dir_path):
            return {
                "status": "error",
                "detail": "Invalid agent name",
                "data": None
            }
        
        # 如果指定语言的文件不存在，降级到默认文件
        if not guidance_file.is_file():
            guidance_file = (guidance_dir_path / f"{agent_name}.md").resolve()
            # 再次验证降级后的文件路径
            if not guidance_file.is_relative_to(guidance_dir_path):
                return {
                    "status": "error",
                    "detail": "Invalid agent name",
                    "data": None
                }
        
        # 检查文件是否存在
        if not guidance_file.is_file():
            return {
                "status": "error",
                "detail": f"Guidance file not found for agent: {agent_name}",
                "data": None
            }
        
        # 读取markdown文件内容
        content = guidance_file.read_text(encoding="utf-8")
        return {
            "status": "success",
            "data": content,
            "agent_name": agent_name,
            "language": language
        }
    except Exception as e:
        return {
            "status": "error",
            "detail": f"Failed to load guidance: {str(e)}",
            "data": None
        }


# 请求模型定义
class AgentRecommendationRequest(BaseModel):
    """Agent 推荐请求模型"""
    requirement: str

@router.post("/recommend_agents")
async def recommend_agents(
    request: AgentRecommendationRequest,
    current_user_id: Annotated[str, Depends(get_current_user)]
) -> Dict[str, Any]:
    """
    根据用户需求文本，推荐适合完成任务的 agent 列表
    Request Body:
        requirement: 用户的需求描述文本
    Returns:
        包含推荐 agent 名称列表和分析理由的响应对象
    """
    
    try:
        # 验证输入
        task_desc = request.requirement
        if not task_desc or not task_desc.strip():
            return {
                "status": "error",
                "detail": "requirement 不能为空",
                "data": None
            }
        
        # 检查输入字符串长度（防止恶意调用）
        if len(task_desc) > 200:
            return {
                "status": "error",
                "detail": "需求描述长度不能超过200个字符",
                "data": None
            }
        
        # 检测用户输入的语言（检查是否包含中文字符）
        import unicodedata
        has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in task_desc)
        lang = "zh" if has_chinese else "en"
        
        # 加载配置
        profiles = load_profiles()
        if not profiles:
            return {
                "status": "error",
                "detail": "无法加载 agent profiles 配置",
                "data": None
            }
        
        model_config = load_model_config_from_yaml("agent_recommender")
        if not model_config:
            return {
                "status": "error",
                "detail": "无法加载 agent_recommender 模型配置",
                "data": None
            }
        
        # 构建 agent 描述文本（根据检测到的语言）
        agent_descriptions = build_agent_descriptions(profiles, language=lang)
        
        # 构建中文名称到英文名称的映射（用于后续转换）
        chinese_to_english_map = {}
        for agent_name, agent_info in profiles.items():
            if agent_info.get("is_active") is False:
                continue
            zh_info = agent_info.get("zh", {})
            nick_name = zh_info.get("nick_name", "")
            if nick_name:
                chinese_to_english_map[nick_name] = agent_name
        
        # 根据语言构建相应的 prompt
        if lang == "zh":
            system_prompt = f"""你是一个专业的 AI Agent 匹配助手。

以下是可用的 Agent 列表：
{agent_descriptions}

根据用户的需求描述，从上述 Agent 列表中，推荐最适合完成该任务的 Agent。
你必须严格按照 JSON 格式返回结果，不要包含任何其他文本。
注意：recommended_agents 列表中必须只用英文agent名称（如Lucy、Bento），不要用中文名称。
返回格式必须是：{{"recommended_agents": ["Lucy", "Bento"], "reason": "选择这些Agent的理由（用中文和中文Agent名称，如本托）"}}"""
            
            user_prompt = f"""用户需求: {task_desc}

请分析用户的需求，从上述 Agent 列表中推荐最适合的一个或多个 Agent。
重要：recommended_agents 列表中只能用英文名称（如Lucy、Bento），reason中可以用中文名称（如本托、艾莉亚）。
请按以下 JSON 格式返回（不要包含任何 markdown 格式或其他文本）：
{{"recommended_agents": ["Lucy", "Bento"], "reason": "选择理由（用中文）"}}"""
        else:
            system_prompt = f"""You are a professional AI Agent matching assistant.

Here is the list of available Agents:
{agent_descriptions}

Based on the user's requirement description, recommend the most suitable Agent(s) from the above list to complete the task.
You must strictly follow JSON format and return only JSON without any other text.
Note: The recommended_agents list must only use English agent names (e.g., Lucy, Bento), not Chinese names.
Return format must be: {{"recommended_agents": ["Lucy", "Bento"], "reason": "Reason for selecting these Agents (in English)"}}"""
            
            user_prompt = f"""User Requirement: {task_desc}

Please analyze the user's requirement and recommend the most suitable Agent(s) from the above list.
Important: The recommended_agents list must only contain English names (e.g., Lucy, Bento).
Please return in the following JSON format (no markdown or other text):
{{"recommended_agents": ["Lucy", "Bento"], "reason": "Reason for selection (English)"}}"""
        
        # 调用 LLM API
        response = await request_LLM_api(
            model_config,
            user_prompt,
            system_prompt,
            response_format={"type": "json_object"},
            enable_thinking=False,
            enable_search=False
        )
        
        if not response:
            return {
                "status": "error",
                "detail": "LLM API 请求失败",
                "data": None
            }
        
        # 解析 LLM 返回的 JSON
        try:
            # 尝试直接解析
            result = json.loads(response)
        except json.JSONDecodeError:
            # 如果直接解析失败，尝试提取 JSON
            json_match = re.search(r'\{[^{}]*"recommended_agents"[^{}]*\}', response, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                except json.JSONDecodeError:
                    return {
                        "status": "error",
                        "detail": f"无法解析 LLM 返回的 JSON: {response[:200]}",
                        "data": None
                    }
            else:
                return {
                    "status": "error",
                    "detail": f"LLM 返回格式不符合要求: {response[:200]}",
                    "data": None
                }
        
        # 验证返回的 agent 名称是否有效
        if "recommended_agents" in result:
            valid_agents = []
            for agent_name in result["recommended_agents"]:
                # 首先尝试直接匹配英文名称
                if agent_name in profiles and profiles[agent_name].get("is_active") is not False:
                    valid_agents.append(agent_name)
                # 如果是中文名称，尝试转换为英文
                elif agent_name in chinese_to_english_map:
                    english_name = chinese_to_english_map[agent_name]
                    valid_agents.append(english_name)
            result["recommended_agents"] = valid_agents
        
        return {
            "status": "success",
            "data": result,
            "requirement": task_desc,
            "language": lang
        }
    
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "detail": f"处理请求时发生错误: {str(e)}",
            "trace": traceback.format_exc(),
            "data": None
        }
