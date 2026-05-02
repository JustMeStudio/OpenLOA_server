import os
import sqlite3
from dotenv import load_dotenv


load_dotenv()
DB_PATH = os.getenv("DB_PATH")



def delete_table(table_name):
    """
    从数据库中删除指定的表
    """
    # 1. 安全校验：防止 SQL 注入
    # 表名不能使用参数化查询（?），所以需要手动校验字符合法性
    import re
    if not re.match(r'^[a-zA-Z0-9_]+$', table_name):
        print(f"❌ 错误：非法的表名 '{table_name}'")
        return

    # 2. 执行删除
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            # 使用 IF EXISTS 防止表不存在时报错
            cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            print(f"✅ 表 '{table_name}' 已成功从数据库中删除（如果它存在的话）")
    except sqlite3.Error as e:
        print(f"❌ 删除表失败: {e}")

# 调用示例：
delete_table("agents")