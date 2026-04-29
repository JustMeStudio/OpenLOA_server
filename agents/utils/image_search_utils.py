"""
图片搜索工具模块 - 支持从Unsplash和Pexels搜索高质量配图
被多个agent的tools所共享使用，降低代码重复率
"""
import os
import asyncio
import random
import httpx
from agents.utils.com import request_LLM_api
from agents.utils.config import load_tool_config

UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")
UNSPLASH_API_BASE = os.getenv("UNSPLASH_API_BASE", "https://api.unsplash.com")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PEXELS_API_BASE = os.getenv("PEXELS_API_BASE", "https://api.pexels.com/v1")

# 加载翻译模型配置（各agent可能使用的相同translator）
translator_model = load_tool_config("translator")  # 支持所有agent共用


async def search_pictures(image_requests: list[dict], orientation: str = "landscape", source: str = "pexels") -> dict:
    """
    输入图片内容描述列表，自动从指定图源搜索匹配图片，验证有效性后返回结果。
    image_requests: [{"desc": "一只在草地上奔跑的金毛寻回犬"}, ...]
    orientation: "landscape" / "portrait" / "squarish"
    source: "unsplash" 或 "pexels"
    Returns: {"result": "success", "pictures": [{"desc": "...", "url": "...", "attribution": {...}}, ...]}
    """
    if not image_requests:
        return {"result": "failure", "message": "没有图片请求", "pictures": []}

    # 1. 批量将描述翻译为英文搜索关键词（一次 LLM 调用）
    descs = [item["desc"] for item in image_requests]
    keywords = await _translate_to_search_keywords(descs)

    # 2. 并发搜索 + 验证
    if source == "pexels":
        tasks = [
            _search_and_validate_pexels(item["desc"], keyword, orientation)
            for item, keyword in zip(image_requests, keywords)
        ]
    else:
        tasks = [
            _search_and_validate_unsplash(item["desc"], keyword, orientation)
            for item, keyword in zip(image_requests, keywords)
        ]
    results = await asyncio.gather(*tasks)
    return {"result": "success", "pictures": list(results)}


async def _translate_to_search_keywords(descs: list[str]) -> list[str]:
    """批量将图片描述转化为适合搜索引擎的简洁英文关键词"""
    desc_list = "\n".join(f"{i+1}. {desc}" for i, desc in enumerate(descs))
    prompt = f"""请将以下图片描述转化为适合图片搜索引擎的简洁英文关键词（2-4个词），每行一个，仅输出关键词，不要编号、标点或任何解释：\n\n{desc_list}"""
    system_prompt = "你是翻译助手，将图片描述转化为简洁英文搜索关键词。输出行数必须与输入行数完全一致，每行只输出一个关键词短语。"

    result = await request_LLM_api(translator_model, prompt, system_prompt, enable_search=False, enable_thinking=False)
    if not result:
        return descs  # 翻译失败时用原描述兜底

    lines = [line.strip() for line in result.strip().split("\n") if line.strip()]
    # 数量不足时用原描述补充
    while len(lines) < len(descs):
        lines.append(descs[len(lines)])
    return lines[:len(descs)]


async def _search_and_validate_unsplash(desc: str, keyword: str, orientation: str) -> dict:
    """搜索单个关键词并验证图片有效性，返回第一个有效图片URL及署名信息"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        # 调用 Unsplash Search API
        try:
            resp = await client.get(
                f"{UNSPLASH_API_BASE}/search/photos",
                params={
                    "query": keyword,
                    "per_page": 5,
                    "orientation": orientation,
                    "content_filter": "high",
                    "order_by": "relevant"
                },
                headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
            )
            resp.raise_for_status()
            data = resp.json()
            # 提取候选图片信息：url、photo_id、摄影师署名、分辨率
            candidates = [
                {
                    "url": photo["urls"]["regular"],
                    "photo_id": photo["id"],
                    "photographer": photo["user"]["name"],
                    "photographer_url": photo["user"]["links"]["html"] + "?utm_source=loa&utm_medium=referral",
                    "width": photo.get("width"),
                    "height": photo.get("height")
                }
                for photo in data.get("results", [])
            ]
        except Exception as e:
            print(f"Unsplash 搜索失败 [{keyword}]: {str(e)}")
            return {"desc": desc, "url": None, "attribution": None}

        if not candidates:
            return {"desc": desc, "url": None, "attribution": None}

        # 验证所有候选图片，收集通过验证的结果
        valid_candidates = []
        for candidate in candidates:
            if await _validate_image_url(client, candidate["url"]):
                valid_candidates.append(candidate)
        
        # 从通过验证的候选中随机选择，若都不通过则从所有候选中随机选
        if valid_candidates:
            chosen = random.choice(valid_candidates)
        else:
            chosen = random.choice(candidates)  # 都不通过时退而求其次

        # 触发 Unsplash 下载事件（合规要求，fire-and-forget）
        try:
            await client.get(
                f"{UNSPLASH_API_BASE}/photos/{chosen['photo_id']}/download",
                headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
                timeout=5.0
            )
        except Exception as e:
            print(f"Unsplash 下载触发失败（非致命）: {str(e)}")

        return {
            "desc": desc,
            "url": chosen["url"],
            "resolution": f"{chosen['width']}x{chosen['height']}",
            "attribution": {
                "photographer": chosen["photographer"],
                "photographer_url": chosen["photographer_url"],
                "source_url": "https://unsplash.com/?utm_source=loa&utm_medium=referral",
                "source_name": "Unsplash"
            }
        }


async def _validate_image_url(client: httpx.AsyncClient, url: str) -> bool:
    """HEAD 请求验证图片 URL 有效性：状态码 200 + Content-Type 为图片 + 文件大小 > 1KB"""
    try:
        resp = await client.head(url, timeout=5.0, follow_redirects=True)
        if resp.status_code != 200:
            return False
        content_type = resp.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            return False
        content_length = int(resp.headers.get("content-length", 0))
        if 0 < content_length < 1024:  # 过滤占位小图
            return False
        return True
    except Exception:
        return False


async def _search_and_validate_pexels(desc: str, keyword: str, orientation: str) -> dict:
    """从 Pexels 搜索单个关键词并验证图片有效性，返回图片URL及署名信息"""
    # Pexels orientation 参数：landscape / portrait / square（不是 squarish）
    pexels_orientation = "square" if orientation == "squarish" else orientation
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                f"{PEXELS_API_BASE}/search",
                params={
                    "query": keyword,
                    "per_page": 5,
                    "orientation": pexels_orientation,
                },
                headers={"Authorization": PEXELS_API_KEY}
            )
            resp.raise_for_status()
            data = resp.json()
            # 取 large 尺寸（940px宽），适合PPT/海报使用
            candidates = [
                {
                    "url": photo["src"]["large"],
                    "photographer": photo["photographer"],
                    "photographer_url": photo["photographer_url"],
                    "photo_page_url": photo["url"],
                    "width": photo.get("width"),
                    "height": photo.get("height")
                }
                for photo in data.get("photos", [])
            ]
        except Exception as e:
            print(f"Pexels 搜索失败 [{keyword}]: {str(e)}")
            return {"desc": desc, "url": None, "attribution": None}

        if not candidates:
            return {"desc": desc, "url": None, "attribution": None}

        # 验证所有候选图片，收集通过验证的结果
        valid_candidates = []
        for candidate in candidates:
            if await _validate_image_url(client, candidate["url"]):
                valid_candidates.append(candidate)
        
        # 从通过验证的候选中随机选择，若都不通过则从所有候选中随机选
        if valid_candidates:
            chosen = random.choice(valid_candidates)
        else:
            chosen = random.choice(candidates)  # 都不通过时退而求其次

        return {
            "desc": desc,
            "url": chosen["url"],
            "resolution": f"{chosen['width']}x{chosen['height']}",
            "attribution": {
                "photographer": chosen["photographer"],
                "photographer_url": chosen["photographer_url"] + "?utm_source=loa&utm_medium=referral",
                "source_url": "https://www.pexels.com/?utm_source=loa&utm_medium=referral",
                "source_name": "Pexels"
            }
        }
