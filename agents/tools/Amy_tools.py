import os
import asyncio
import sqlite3
from dotenv import load_dotenv
from agents.globals.context import add_pending_message

load_dotenv()

_main_db_path = os.getenv("DB_PATH", "database/main.db")
AIRPORT_DB_PATH = os.path.join(os.path.dirname(_main_db_path), "airport.db")


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

async def get_database_schema() -> dict:
    """
    获取机场数据库的完整表结构，包括所有表名及其每个字段的名称、类型、是否非空、是否主键。
    在生成 SQL 查询之前应首先调用此工具，以确保 SQL 语句与真实表结构一致。
    """
    def _fetch():
        if not os.path.exists(AIRPORT_DB_PATH):
            return {"result": "failure", "message": "机场数据库尚未初始化，请先运行 0_init_airport_db.py"}
        conn = sqlite3.connect(AIRPORT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        schema = {}
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table})")
            cols = cursor.fetchall()
            schema[table] = [
                {"name": c[1], "type": c[2], "notnull": bool(c[3]), "pk": bool(c[5])}
                for c in cols
            ]
        conn.close()
        return {"result": "success", "tables": tables, "schema": schema}

    return await asyncio.to_thread(_fetch)


async def execute_sql_query(sql: str) -> dict:
    """
    执行一条 SQLite SELECT 查询语句，返回查询结果行列表。
    若 SQL 存在语法错误或字段/表名不正确，将返回具体的错误信息，供调用方修正后重试。
    仅允许 SELECT 语句，禁止任何写操作（INSERT / UPDATE / DELETE / DROP 等）。
    """
    stripped = sql.strip().upper()
    for kw in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "ATTACH", "PRAGMA"):
        if stripped.startswith(kw) or f" {kw} " in stripped:
            return {
                "result": "failure",
                "error": f"出于安全考虑，不允许执行 {kw} 等写操作语句。"
            }

    def _run():
        if not os.path.exists(AIRPORT_DB_PATH):
            return {"result": "failure", "error": "机场数据库尚未初始化，请先运行 0_init_airport_db.py"}
        try:
            conn = sqlite3.connect(AIRPORT_DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("PRAGMA query_only = ON")
            cursor.execute(sql)
            rows = [dict(r) for r in cursor.fetchall()]
            conn.close()
            return {"result": "success", "rows": rows, "count": len(rows)}
        except sqlite3.Error as e:
            return {"result": "failure", "error": f"SQL 执行错误: {str(e)}"}

    return await asyncio.to_thread(_run)


async def generate_chart(chart_type: str, title: str, data: dict) -> dict:
    """
    根据给定的数据和图表类型，生成要在前端渲染的图表结构。
    前端接收到此返回后将直接进行图表渲染。
    """
    try:
        chart_payload = {
            "action": "render_chart",
            "chart_type": chart_type,
            "title": title,
            "data": data
        }

        return {
            "result": "success",
            "file_attachment": chart_payload,
            "message": "图表已生成并推送给前端渲染。"
        }
    except Exception as e:
        return {
            "result": "failure",
            "error": f"生成图表时发生错误: {str(e)}"
        }


# ---------------------------------------------------------------------------
# Tool registry & schema
# ---------------------------------------------------------------------------

tool_registry = {
    "get_database_schema": get_database_schema,
    "execute_sql_query": execute_sql_query,
    "generate_chart": generate_chart,
}

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_database_schema",
            "description": (
                "获取机场数据库的完整表结构（所有表名 + 每张表的字段名、类型、约束）。"
                "在构造 SQL 查询前必须先调用此工具，确保字段名和表名准确无误。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_sql_query",
            "description": (
                "执行一条 SQLite SELECT 语句，返回查询结果。"
                "若返回 result=failure 并附带 error 字段，说明 SQL 有误，应根据错误信息修正后重新调用。"
                "只允许 SELECT，禁止任何写操作。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "要执行的 SQLite SELECT 语句，字段名和表名须与 get_database_schema 返回的结构严格一致"
                    }
                },
                "required": ["sql"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_chart",
            "description": (
                "将查询到的数据生成图表返回给前端渲染。支持柱状图(bar)、折线图(line)和饼状图(pie)。\n"
                "传入的 data 结构必须严格符合以下示例：\n"
                "- bar / line 图表示例: {\"labels\": [\"北京\", \"上海\"], \"datasets\": [{\"label\": \"2023年销售额\", \"data\": [100, 200]}, {\"label\": \"2024年销售额\", \"data\": [150, 250]}]}\n"
                "- pie 图表示例: {\"labels\": [\"餐饮\", \"交通\"], \"values\": [30, 70]}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "line", "pie"],
                        "description": "图表类型"
                    },
                    "title": {
                        "type": "string",
                        "description": "图表的标题"
                    },
                    "data": {
                        "type": "object",
                        "description": "图表的结构化数据，必须符合请求的图表类型的结构要求"
                    }
                },
                "required": ["chart_type", "title", "data"]
            }
        }
    },
]
