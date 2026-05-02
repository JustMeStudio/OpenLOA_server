import sqlite3
import os

# 你的数据库路径
DB_PATH = 'database/main.db'

def add_agent_column(db_path):
    # 检查文件是否存在
    if not os.path.exists(db_path):
        print(f"错误: 找不到数据库文件 {db_path}")
        return

    try:
        # 连接数据库
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 1. 检查字段是否已经存在 (防止重复运行脚本报错)
        cursor.execute("PRAGMA table_info(conversations)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'agent' in columns:
            print("提示: 'agent' 字段已经存在于 'conversations' 表中。")
        else:
            # 2. 执行添加字段的操作
            # 假设 agent 字段是文本类型 (TEXT)
            alter_query = "ALTER TABLE conversations ADD COLUMN agent TEXT"
            cursor.execute(alter_query)
            conn.commit()
            print("成功: 已向 'conversations' 表添加 'agent' 字段。")

    except sqlite3.Error as e:
        print(f"数据库错误: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    add_agent_column(DB_PATH)