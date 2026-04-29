"""
知识库删除脚本
按 ID 从 ChromaDB 知识库中删除指定语料

用法：
  1. 先运行 list_knowledge.py 获取要删除条目的 ID
  2. 将 ID 填入下方 DELETE_IDS 列表
  3. 运行本脚本
"""

import asyncio
import aiohttp
import json
import sys

# ===== 配置区 =====
BASE_URL = "http://localhost:9000"
ADMIN_ACCOUNT = "your_admin@example.com"
ADMIN_PASSWORD = "your_password"

# 填入要删除的语料 ID（从 list_knowledge.py 输出中复制）
DELETE_IDS = [
    "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    # "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
]
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


async def delete_knowledge(session: aiohttp.ClientSession, token: str):
    headers = {"Authorization": f"Bearer {token}"}

    print(f"\n🗑️  准备删除 {len(DELETE_IDS)} 条语料：")
    for id_ in DELETE_IDS:
        print(f"   - {id_}")

    confirm = input("\n确认删除？(y/n): ").strip().lower()
    if confirm != "y":
        print("❎ 已取消")
        return

    async with session.request(
        "DELETE",
        f"{BASE_URL}/admin/knowledge-base/delete",
        json={"ids": DELETE_IDS},
        headers=headers
    ) as resp:
        data = await resp.json()
        if resp.status == 200 and data.get("status") == "success":
            print(f"\n✅ 删除成功，已删除 ID：")
            for id_ in data.get("deleted_ids", []):
                print(f"   - {id_}")
        else:
            print(f"\n❌ 删除失败 ({resp.status}): {json.dumps(data, ensure_ascii=False)}")


async def main():
    print("=" * 60)
    print("🗑️  知识库删除工具")
    print("=" * 60)

    if not DELETE_IDS or DELETE_IDS == ["xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"]:
        print("⚠️  请先在脚本中填写要删除的 DELETE_IDS")
        sys.exit(1)

    async with aiohttp.ClientSession() as session:
        token = await get_admin_token(session)
        await delete_knowledge(session, token)


if __name__ == "__main__":
    asyncio.run(main())
