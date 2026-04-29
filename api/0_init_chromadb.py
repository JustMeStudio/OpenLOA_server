import os
import chromadb
from dotenv import load_dotenv
from fastapi import APIRouter

router = APIRouter()

load_dotenv()


def init_chromadb():
    chroma_path = os.getenv("CHROMADB_PATH_CS", "database/chromadb_cs")
    os.makedirs(chroma_path, exist_ok=True)
    client = chromadb.PersistentClient(path=chroma_path)
    client.get_or_create_collection(name="knowledge_base")
    print(f"✅ ChromaDB Initialized Successfully. Path: {chroma_path}")


init_chromadb()
