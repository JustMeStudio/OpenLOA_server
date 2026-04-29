"""
管理员接口
支持查看限流配置和状态，以及管理 Lucy 客服知识库
"""
import os
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Annotated, List, Optional
from openai import AsyncOpenAI
import chromadb
from dotenv import load_dotenv

from .security import get_current_admin
from utils.generation_manager import get_semaphore_status
from utils.config_loader import get_all_config
from agents.utils.config import load_tool_config

load_dotenv()

router = APIRouter()

# ===== 知识库工具函数 =====

def _get_chroma_collection():
    """获取 ChromaDB collection，不存在则自动创建"""
    chroma_path = os.getenv("CHROMADB_PATH_CS", "database/chromadb_cs")
    client = chromadb.PersistentClient(path=chroma_path)
    return client.get_or_create_collection(name="knowledge_base")


async def _embed_texts(texts: List[str]) -> List[List[float]]:
    """调用 Dashscope Embedding 模型批量向量化文本"""
    config = load_tool_config("Lucy_embedding")
    client = AsyncOpenAI(
        api_key=config.get("api_key"),
        base_url=config.get("base_url"),
    )
    response = await client.embeddings.create(
        model=config.get("model"),
        input=texts,
    )
    return [item.embedding for item in response.data]


# ===== 查看限流状态 =====
@router.get("/rate-limit/status")
async def get_rate_limit_status(
    current_admin_id: Annotated[str, Depends(get_current_admin)]
):
    """查看当前限流状态"""
    status = await get_semaphore_status()
    config = await get_all_config()
    
    return {
        "status": "success",
        "data": {
            "agents": status,
            "config": config
        }
    }


# ===== 查看配置文件内容 =====
@router.get("/rate-limit/config")
async def get_rate_limit_config(
    current_admin_id: Annotated[str, Depends(get_current_admin)]
):
    """查看当前配置文件内容"""
    config = await get_all_config()
    return {
        "status": "success",
        "data": config
    }


# ===== 知识库管理 =====

class KnowledgeEntry(BaseModel):
    embed_content: str
    full_content: Optional[str] = None  # 不填时默认与 embed_content 相同
    metadata: Optional[dict] = {}

class AddKnowledgeRequest(BaseModel):
    entries: List[KnowledgeEntry]

class DeleteKnowledgeRequest(BaseModel):
    ids: List[str]


@router.post("/knowledge-base/add")
async def add_knowledge(
    body: AddKnowledgeRequest,
    current_admin_id: Annotated[str, Depends(get_current_admin)]
):
    """批量插入语料到 ChromaDB 知识库"""
    if not body.entries:
        raise HTTPException(status_code=400, detail="entries 不能为空")

    embed_texts = [e.embed_content for e in body.entries]
    metadatas = []
    for e in body.entries:
        meta = dict(e.metadata or {})
        # 将完整内容存入 metadata；未填则多存一份 embed_content 作为备用
        meta["full_content"] = e.full_content if e.full_content else e.embed_content
        metadatas.append(meta)
    ids = [str(uuid.uuid4()) for _ in body.entries]

    try:
        embeddings = await _embed_texts(embed_texts)
        collection = _get_chroma_collection()
        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=embed_texts,   # documents 存放 embed_content，供 ChromaDB 内部相似度索引用
            metadatas=metadatas,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"插入失败: {str(e)}")

    return {
        "status": "success",
        "inserted_count": len(ids),
        "ids": ids
    }


@router.delete("/knowledge-base/delete")
async def delete_knowledge(
    body: DeleteKnowledgeRequest,
    current_admin_id: Annotated[str, Depends(get_current_admin)]
):
    """按 ID 删除知识库中的语料"""
    if not body.ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")

    try:
        collection = _get_chroma_collection()
        collection.delete(ids=body.ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")

    return {
        "status": "success",
        "deleted_ids": body.ids
    }


@router.get("/knowledge-base/list")
async def list_knowledge(
    current_admin_id: Annotated[str, Depends(get_current_admin)],
    limit: int = 20,
    offset: int = 0
):
    """查看知识库中已有的语料列表（不返回向量）"""
    try:
        collection = _get_chroma_collection()
        total = collection.count()
        result = collection.get(
            limit=limit,
            offset=offset,
            include=["documents", "metadatas"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")

    entries = []
    for doc_id, doc, meta in zip(result["ids"], result["documents"], result["metadatas"]):
        full_content = meta.get("full_content") if meta else None
        # 展示时将 full_content 从 metadata 里提取到顶层，方便阅读
        extra_meta = {k: v for k, v in (meta or {}).items() if k != "full_content"}
        entries.append({
            "id": doc_id,
            "embed_content": doc,
            "full_content": full_content if full_content else doc,
            "metadata": extra_meta
        })

    return {
        "status": "success",
        "total": total,
        "limit": limit,
        "offset": offset,
        "entries": entries
    }
