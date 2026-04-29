"""
知识库录入脚本
从 xlsx 文件中读取语料并批量插入 ChromaDB 知识库

xlsx 格式要求（表头在第一行）：
  必填列：embed_content     — 用于向量化检索的精简文本
  可选列：full_content      — 返回给 AI 的完整内容（不填则与 embed_content 相同）
  可选列：category          — 语料分类标签，会写入 metadata（可自定义任意列名作为 metadata）

示例：
  | embed_content          | full_content                          | category |
  |------------------------|---------------------------------------|----------|
  | 充值方式 微信 支付宝    | 本网站支持微信和支付宝两种充值方式... | 充值     |
"""

import asyncio
import aiohttp
import pandas as pd
import json
import sys
import os

# ===== 配置区 =====
BASE_URL = "http://localhost:9000"
ADMIN_ACCOUNT = "your_admin@example.com"   # 管理员账号
ADMIN_PASSWORD = "your_password"            # 管理员密码

XLSX_PATH = r"D:\代码库\loa_server\debug_tools\query\admin\knowledge_data.xlsx"          # xlsx 文件路径（相对或绝对路径）
SHEET_NAME = 0                             # 读取第几个 sheet（0 = 第一个），也可以填 sheet 名称
BATCH_SIZE = 20                            # 每批插入条数（避免单次请求过大）

# embed_content 列名（必须存在）
EMBED_COL = "embed_content"
# full_content 列名（可选，不存在则忽略）
FULL_COL  = "full_content"
# 除以上两列外，其余列全部作为 metadata 字段
# ====================


async def get_admin_token(session: aiohttp.ClientSession) -> str:
    """登录并获取 access_token"""
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


def load_xlsx(path: str) -> list[dict]:
    """读取 xlsx，返回 entries 列表"""
    if not os.path.exists(path):
        print(f"❌ 文件不存在: {path}")
        sys.exit(1)

    df = pd.read_excel(path, sheet_name=SHEET_NAME, dtype=str)
    df = df.where(pd.notna(df), None)  # 将 NaN 转为 None

    if EMBED_COL not in df.columns:
        print(f"❌ xlsx 中缺少必填列：{EMBED_COL}")
        print(f"   现有列：{list(df.columns)}")
        sys.exit(1)

    # 剩余列作为 metadata
    meta_cols = [c for c in df.columns if c not in (EMBED_COL, FULL_COL)]

    entries = []
    for _, row in df.iterrows():
        embed_text = str(row[EMBED_COL]).strip() if row[EMBED_COL] else None
        if not embed_text:
            continue  # 跳过空行

        full_text = str(row[FULL_COL]).strip() if FULL_COL in df.columns and row.get(FULL_COL) else None

        metadata = {}
        for col in meta_cols:
            val = row.get(col)
            if val is not None:
                metadata[col] = str(val).strip()

        entry = {"embed_content": embed_text}
        if full_text:
            entry["full_content"] = full_text
        if metadata:
            entry["metadata"] = metadata

        entries.append(entry)

    print(f"📄 共读取到 {len(entries)} 条有效语料（跳过空行后）")
    return entries


async def add_entries(session: aiohttp.ClientSession, token: str, entries: list[dict]):
    """分批插入语料"""
    headers = {"Authorization": f"Bearer {token}"}
    total = len(entries)
    success_ids = []

    for start in range(0, total, BATCH_SIZE):
        batch = entries[start: start + BATCH_SIZE]
        batch_num = start // BATCH_SIZE + 1
        print(f"\n📤 正在上传第 {batch_num} 批（{start + 1}~{min(start + BATCH_SIZE, total)} / {total}）...")

        async with session.post(
            f"{BASE_URL}/admin/knowledge-base/add",
            json={"entries": batch},
            headers=headers
        ) as resp:
            data = await resp.json()
            if resp.status == 200 and data.get("status") == "success":
                ids = data.get("ids", [])
                success_ids.extend(ids)
                print(f"   ✅ 插入成功 {len(ids)} 条")
            else:
                print(f"   ❌ 插入失败 ({resp.status}): {json.dumps(data, ensure_ascii=False)}")

    print(f"\n🎉 全部完成！成功插入 {len(success_ids)} / {total} 条语料")


async def main():
    print("=" * 60)
    print("📚 知识库批量录入工具")
    print("=" * 60)

    entries = load_xlsx(XLSX_PATH)
    if not entries:
        print("⚠️  没有可录入的数据，退出。")
        return

    async with aiohttp.ClientSession() as session:
        token = await get_admin_token(session)
        await add_entries(session, token, entries)


if __name__ == "__main__":
    asyncio.run(main())
