"""
知识库查看脚本
分页列出 ChromaDB 知识库中已有的语料
"""

import asyncio
import aiohttp
import json
import sys

# ===== 配置区 =====
BASE_URL = "http://localhost:9000"
ADMIN_ACCOUNT = "your_admin@example.com"
ADMIN_PASSWORD = "your_password"

PAGE_LIMIT  = 20   # 每页条数
PAGE_OFFSET = 0    # 从第几条开始（0 = 第一条）
# ====================


async def get_admin_token(session: aiohttp.ClientSession) -> str:
    async with session.post(
        f"{BASE_URL}/account/login",
        json={"account": ADMIN_ACCOUNT, "password": ADMIN_PASSWORD}
    ) as resp:
        if resp.status != 200:
            text = await resp.text()
            print(f"❌ 登录失败 ({resp.status}): {text}")
            sys.exit(1)
        data = await resp.json()
        token = data.get("access_token")
        print(f"✅ 登录成功，Token: {token[:30]}...")
        return token


async def list_knowledge(session: aiohttp.ClientSession, token: str):
    headers = {"Authorization": f"Bearer {token}"}

    async with session.get(
        f"{BASE_URL}/admin/knowledge-base/list",
        params={"limit": PAGE_LIMIT, "offset": PAGE_OFFSET},
        headers=headers
    ) as resp:
        data = await resp.json()
        if resp.status != 200 or data.get("status") != "success":
            print(f"❌ 查询失败 ({resp.status}): {json.dumps(data, ensure_ascii=False)}")
            return

    total   = data["total"]
    entries = data["entries"]
    print(f"\n📊 知识库共 {total} 条语料，当前显示第 {PAGE_OFFSET + 1}～{PAGE_OFFSET + len(entries)} 条\n")
    print("-" * 80)

    for i, entry in enumerate(entries, start=PAGE_OFFSET + 1):
        print(f"[{i}] ID: {entry['id']}")
        print(f"    embed_content : {entry['embed_content']}")
        full = entry.get("full_content", "")
        if full and full != entry["embed_content"]:
            # 超过 100 字截断显示
            preview = full if len(full) <= 100 else full[:100] + "..."
            print(f"    full_content  : {preview}")
        if entry.get("metadata"):
            print(f"    metadata      : {entry['metadata']}")
        print()

    print("-" * 80)
    remaining = total - PAGE_OFFSET - len(entries)
    if remaining > 0:
        print(f"💡 还有 {remaining} 条未显示，可调整 PAGE_OFFSET 继续查看")


async def main():
    print("=" * 60)
    print("📖 知识库查看工具")
    print("=" * 60)
    async with aiohttp.ClientSession() as session:
        token = await get_admin_token(session)
        await list_knowledge(session, token)


if __name__ == "__main__":
    asyncio.run(main())
