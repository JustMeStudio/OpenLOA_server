import os
import asyncio
import oracledb
from dotenv import load_dotenv
from agents.utils.com import request_LLM_api
from agents.utils.config import load_model_config
from agents.utils.html_converter import html_content_to_pdf

load_dotenv()

# ---------------------------------------------------------------------------
# Oracle connection configuration (from .env)
# ---------------------------------------------------------------------------
# ORACLE_USER     - Oracle 用户名
# ORACLE_PASSWORD - Oracle 密码
# ORACLE_DATABASE - ODS 数据库 Service Name
# ORACLE_HOST     - 访问 IP 地址
# ORACLE_PORT     - 端口号（默认 1521）

def _get_oracle_dsn() -> str:
    host = os.getenv("ORACLE_HOST", "localhost")
    port = int(os.getenv("ORACLE_PORT", "1521"))
    service = os.getenv("ORACLE_DATABASE", "orcl")
    return oracledb.makedsn(host, port, service_name=service)

def _get_oracle_connection():
    user = os.getenv("ORACLE_USER")
    password = os.getenv("ORACLE_PASSWORD")
    if not user or not password:
        raise ValueError("ORACLE_USER 或 ORACLE_PASSWORD 未在 .env 中配置")
    dsn = _get_oracle_dsn()
    return oracledb.connect(user=user, password=password, dsn=dsn)


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

async def get_database_schema() -> dict:
    """
    获取 Oracle 机场数据库的完整表结构，包括当前用户下所有表名及其每个字段的名称、类型、是否非空、是否主键。
    在生成 SQL 查询之前应首先调用此工具，以确保 SQL 语句与真实表结构一致。
    """
    def _fetch():
        try:
            conn = _get_oracle_connection()
            cursor = conn.cursor()

            # 获取当前用户所有表名
            cursor.execute(
                "SELECT table_name FROM user_tables ORDER BY table_name"
            )
            tables = [row[0] for row in cursor.fetchall()]

            schema = {}
            # 获取主键列
            cursor.execute(
                """
                SELECT ucc.table_name, ucc.column_name
                FROM user_constraints uc
                JOIN user_cons_columns ucc
                  ON uc.constraint_name = ucc.constraint_name
                WHERE uc.constraint_type = 'P'
                """
            )
            pk_cols = {(r[0], r[1]) for r in cursor.fetchall()}

            for table in tables:
                cursor.execute(
                    """
                    SELECT column_name, data_type, nullable
                    FROM user_tab_columns
                    WHERE table_name = :tname
                    ORDER BY column_id
                    """,
                    tname=table
                )
                cols = cursor.fetchall()
                schema[table] = [
                    {
                        "name": c[0],
                        "type": c[1],
                        "notnull": (c[2] == "N"),
                        "pk": ((table, c[0]) in pk_cols)
                    }
                    for c in cols
                ]

            cursor.close()
            conn.close()
            return {"result": "success", "tables": tables, "schema": schema}
        except ValueError as e:
            return {"result": "failure", "message": str(e)}
        except oracledb.Error as e:
            return {"result": "failure", "message": f"Oracle 连接/查询错误: {str(e)}"}

    return await asyncio.to_thread(_fetch)


async def execute_sql_query(sql: str) -> dict:
    """
    执行一条 Oracle SELECT 查询语句，返回查询结果行列表。
    若 SQL 存在语法错误或字段/表名不正确，将返回具体的错误信息，供调用方修正后重试。
    仅允许 SELECT 语句，禁止任何写操作（INSERT / UPDATE / DELETE / DROP 等）。
    注意：Oracle SQL 不使用 LIMIT，请改用 FETCH FIRST N ROWS ONLY 或 ROWNUM <= N 进行行数限制。
    """
    stripped = sql.strip().upper()
    for kw in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "MERGE", "TRUNCATE", "EXECUTE", "GRANT", "REVOKE"):
        if stripped.startswith(kw) or f" {kw} " in stripped:
            return {
                "result": "failure",
                "error": f"出于安全考虑，不允许执行 {kw} 等写操作语句。"
            }

    def _run():
        try:
            conn = _get_oracle_connection()
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            cursor.close()
            conn.close()
            return {"result": "success", "rows": rows, "count": len(rows)}
        except ValueError as e:
            return {"result": "failure", "error": str(e)}
        except oracledb.Error as e:
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


def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


async def generate_pdf_report(report_material: str, report_requirement: str) -> dict:
    """
    生成 PDF 报告：先由 LLM 生成 HTML，再转为 PDF 并上传到 OSS。

    参数:
    - report_material: 报告素材（字符串）
    - report_requirement: 报告要求（字符串）

    返回:
    {
        "result": "success|failure",
        "file_attachment": "pdf_url",
        "message": "..."
    }
    """
    if not (report_material or "").strip():
        return {"result": "failure", "error": "report_material 不能为空"}
    if not (report_requirement or "").strip():
        return {"result": "failure", "error": "report_requirement 不能为空"}

    model_config = load_model_config("Amy_prod")
    if not model_config:
        return {"result": "failure", "error": "未找到 Amy_prod 模型配置，无法生成报告"}

    system_prompt = (
        "你是专业报告写作助手。请严格根据用户提供素材与要求，输出一份可直接打印的完整 HTML 文档。"
        "必须返回可渲染的 HTML，不要返回 Markdown，不要输出解释，不要使用代码块包裹。"
    )
    prompt = (
        "请基于以下内容生成完整 HTML 报告。要求:\n"
        "1) 输出必须是完整 HTML（包含 <html>、<head>、<body>）。\n"
        "2) 使用清晰的标题层级、段落、表格/列表（按内容需要），排版适合 A4 打印。\n"
        "3) 正文默认使用中文，风格专业、可交付。\n"
        "4) 不要出现 ``` 等代码围栏。\n\n"
        f"【报告素材】\n{report_material}\n\n"
        f"【报告要求】\n{report_requirement}\n"
    )

    try:
        html_content = await request_LLM_api(
            model_config=model_config,
            prompt=prompt,
            system_prompt=system_prompt,
            enable_search=False,
            enable_thinking=False,
        )
        html_content = _strip_code_fences(html_content)
        if not html_content:
            return {"result": "failure", "error": "LLM 未返回有效内容，无法生成报告"}

        if "<html" not in html_content.lower():
            html_content = (
                "<!doctype html><html><head><meta charset='utf-8'><title>报告</title></head><body>"
                f"<pre style='white-space: pre-wrap; font-family: sans-serif;'>{html_content}</pre>"
                "</body></html>"
            )

        convert_result = await html_content_to_pdf(
            html_content=html_content,
            file_name_prefix="report",
            pdf_config={
                "format": "A4",
                "print_background": True,
                "margin": {"top": "16mm", "right": "14mm", "bottom": "16mm", "left": "14mm"},
                "wait_until": "networkidle",
                "extra_wait_ms": 800,
            },
        )
        if convert_result.get("result") != "success":
            return {
                "result": "failure",
                "error": convert_result.get("message", "HTML 转 PDF 失败")
            }

        return {
            "result": "success",
            "file_attachment": convert_result.get("file_attachment"),
            "message": "PDF 报告已生成并上传。"
        }
    except Exception as e:
        return {
            "result": "failure",
            "error": f"生成 PDF 报告失败: {str(e)}"
        }


# ---------------------------------------------------------------------------
# Tool registry & schema
# ---------------------------------------------------------------------------

tool_registry = {
    "get_database_schema": get_database_schema,
    "execute_sql_query": execute_sql_query,
    "generate_chart": generate_chart,
    "generate_pdf_report": generate_pdf_report,
}

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_database_schema",
            "description": (
                "获取 Oracle 机场数据库的完整表结构（当前用户下所有表名 + 每张表的字段名、类型、约束）。"
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
                "执行一条 Oracle SELECT 语句，返回查询结果。"
                "若返回 result=failure 并附带 error 字段，说明 SQL 有误，应根据错误信息修正后重新调用。"
                "只允许 SELECT，禁止任何写操作。"
                "注意：Oracle 不支持 LIMIT，请使用 FETCH FIRST N ROWS ONLY 或 WHERE ROWNUM <= N 来限制行数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "要执行的 Oracle SELECT 语句，字段名和表名须与 get_database_schema 返回的结构严格一致"
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
    {
        "type": "function",
        "function": {
            "name": "generate_pdf_report",
            "description": (
                "根据报告素材和报告要求自动生成 PDF 报告。"
                "工具会先调用 LLM 生成 HTML 报告，再转成 PDF 上传到 OSS，"
                "最终在 file_attachment 中返回 PDF 链接。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_material": {
                        "type": "string",
                        "description": "报告素材原文，支持多段文本"
                    },
                    "report_requirement": {
                        "type": "string",
                        "description": "报告要求，如篇幅、结构、重点、语气等"
                    }
                },
                "required": ["report_material", "report_requirement"]
            }
        }
    },
]
