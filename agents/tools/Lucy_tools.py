import os
import asyncio
import sqlite3
import uuid
from openai import AsyncOpenAI
import chromadb
from agents.utils.config import load_tool_config
from dotenv import load_dotenv

load_dotenv()

# 加载 Embedding 模型配置
embedding_config = load_tool_config("Lucy_embedding")

# ChromaDB 本地路径（从环境变量读取）
CHROMADB_PATH_CS = os.getenv("CHROMADB_PATH_CS", "database/chromadb_cs")
DB_PATH = os.getenv("DB_PATH")


async def query_knowledge_base(query: str, top_k: int = 5):
    """
    通过 RAG 从本地 ChromaDB 召回与用户问题最相关的知识片段。
    """
    try:
        # 1. 使用 Embedding 模型对用户 query 进行向量化
        embedding_client = AsyncOpenAI(
            api_key=embedding_config.get("api_key"),
            base_url=embedding_config.get("base_url"),
        )
        embed_response = await embedding_client.embeddings.create(
            model=embedding_config.get("model"),
            input=query,
        )
        query_vector = embed_response.data[0].embedding

        # 2. 连接本地 ChromaDB 并执行向量检索（同步阻塞，放入线程池）
        def _chroma_query():
            chroma_client = chromadb.PersistentClient(path=CHROMADB_PATH_CS)
            try:
                col = chroma_client.get_collection(name="knowledge_base")
            except Exception:
                return None
            return col.query(
                query_embeddings=[query_vector],
                n_results=min(top_k, col.count()),
                include=["documents", "metadatas", "distances"]
            )

        results = await asyncio.to_thread(_chroma_query)
        if results is None:
            return {
                "result": "success",
                "message": "知识库尚未建立或为空，请先向知识库中导入数据。",
                "documents": []
            }

        # 3. 整理检索结果
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        if not documents:
            return {
                "result": "success",
                "message": "未找到与该问题相关的知识库内容。",
                "documents": []
            }

        retrieved = []
        for doc, meta, dist in zip(documents, metadatas, distances):
            # 优先返回 metadata 中存储的完整内容，没有则回退到 embed_content
            full_content = meta.get("full_content") if meta else None
            retrieved.append({
                "content": full_content if full_content else doc,
                "relevance_score": round(1 - dist, 4)
            })

        return {
            "result": "success",
            "query": query,
            "retrieved_count": len(retrieved),
            "documents": retrieved
        }

    except Exception as e:
        return {
            "result": "failure",
            "message": f"知识库检索失败: {str(e)}"
        }


#------------------------------------------------------------------------------------------------------#

async def submit_feedback(content: str, type: str = "other", contact: str = None, user_id: str = None):
    """
    将用户反馈写入数据库的 user_feedback 表。
    """
    try:
        feedback_id = str(uuid.uuid4())

        def _insert():
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO user_feedback (feedback_id, user_id, type, content, contact)
                   VALUES (?, ?, ?, ?, ?)''',
                (feedback_id, user_id, type, content, contact)
            )
            conn.commit()
            conn.close()

        await asyncio.to_thread(_insert)
        return {
            "result": "success",
            "message": "反馈已成功提交，感谢您的宝贵意见！",
            "feedback_id": feedback_id
        }
    except Exception as e:
        return {
            "result": "failure",
            "message": f"反馈提交失败: {str(e)}"
        }


#------------------------------------------------------------------------------------------------------#

tool_registry = {
    "query_knowledge_base": query_knowledge_base,
    "submit_feedback": submit_feedback,
}

tools = [
    {
        "type": "function",
        "function": {
            "name": "query_knowledge_base",
            "description": "通过语义检索从本地知识库（ChromaDB）中召回与用户问题最相关的文档片段，用于辅助回答用户关于网站功能、服务或使用方式的问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用户的问题或检索关键词，越具体越好"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回的最相关文档片段数量，默认为 5",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "submit_feedback",
            "description": "当用户表达意见、建议、投诉或报告问题时，将其反馈内容记录到数据库中",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "用户反馈的具体内容"
                    },
                    "type": {
                        "type": "string",
                        "enum": ["bug", "suggestion", "complaint", "other"],
                        "description": "反馈类型：bug（程序问题）、suggestion（功能建议）、complaint（投诉）、other（其他）",
                        "default": "other"
                    },
                    "contact": {
                        "type": "string",
                        "description": "用户留下的联系方式（邮箱或手机号），可选"
                    }
                },
                "required": ["content"]
            }
        }
    }
]