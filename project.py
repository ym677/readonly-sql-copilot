"""Read-only SQL assistant over a fixed synthetic SQLite database."""

import json
from pathlib import Path
import re
import sqlite3
import time


SPEC = {
    "title": "Read-only SQL Copilot",
    "subtitle": "自然语言查询合成工厂数据，展示 SQL、只读检查与结果表",
    "slug": "readonly-sql-copilot",
    "accent": "#4f8cff",
    "sample_input": "按设备统计平均温度，并按平均温度从高到低排序。",
    "input_label": "输入查询问题，也可以直接输入 SELECT 语句",
}

SCHEMA = """equipment(equipment_id TEXT, name TEXT, workshop TEXT)
measurements(equipment_id TEXT, recorded_at TEXT, temperature REAL, vibration REAL)
maintenance(equipment_id TEXT, status TEXT, task TEXT)"""

ALLOWED_TABLES = {"equipment", "measurements", "maintenance"}
ALLOWED_FUNCTIONS = {
    "count", "avg", "sum", "min", "max", "round", "abs", "lower", "upper",
    "coalesce", "ifnull", "length", "substr", "date", "strftime",
}


def load_factory_data():
    path = Path(__file__).resolve().parent / "data" / "factory.json"
    return json.loads(path.read_text(encoding="utf-8"))


def database_authorizer(action, argument1, argument2, database_name, trigger_name):
    """Whitelist SQLite read operations rather than just checking SQL keywords."""
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and argument1 in ALLOWED_TABLES:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        function_name = (argument2 or argument1 or "").lower()
        if function_name in ALLOWED_FUNCTIONS:
            return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def create_database():
    """The caller never controls a filesystem path or database connection URI."""
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE equipment (equipment_id TEXT PRIMARY KEY, name TEXT, workshop TEXT);
        CREATE TABLE measurements (equipment_id TEXT, recorded_at TEXT, temperature REAL, vibration REAL);
        CREATE TABLE maintenance (equipment_id TEXT, status TEXT, task TEXT);
    """)
    data = load_factory_data()
    connection.executemany(
        "INSERT INTO equipment VALUES (:equipment_id, :name, :workshop)", data["equipment"]
    )
    connection.executemany(
        "INSERT INTO measurements VALUES (:equipment_id, :recorded_at, :temperature, :vibration)",
        data["measurements"],
    )
    connection.executemany(
        "INSERT INTO maintenance VALUES (:equipment_id, :status, :task)", data["maintenance"]
    )
    connection.commit()
    connection.execute("PRAGMA query_only = ON")
    connection.set_authorizer(database_authorizer)
    return connection


def clean_sql(text):
    value = text.strip()
    fence = re.fullmatch(r"```(?:sql)?\s*([\s\S]*?)\s*```", value, flags=re.IGNORECASE)
    if fence:
        value = fence.group(1).strip()
    if not value or len(value) > 4000:
        raise ValueError("SQL 为空或超过 4000 字符。")
    if not re.match(r"^(SELECT|WITH)\b", value, re.IGNORECASE):
        raise ValueError("只接受 SELECT 或非递归 WITH 查询。")
    if "--" in value or "/*" in value:
        raise ValueError("演示接口不接受 SQL 注释。")
    return value


def execute_readonly(sql, row_limit=100):
    query = clean_sql(sql)
    connection = create_database()
    deadline = time.monotonic() + 0.25
    operations = 0

    def stop_expensive_query():
        nonlocal operations
        operations += 1000
        return operations > 100000 or time.monotonic() > deadline

    connection.set_progress_handler(stop_expensive_query, 1000)
    try:
        cursor = connection.execute(query)
        rows = cursor.fetchmany(row_limit + 1)
        truncated = len(rows) > row_limit
        columns = [description[0] for description in cursor.description]
        return {"columns": columns, "rows": [list(row) for row in rows[:row_limit]], "truncated": truncated}
    except sqlite3.Error as error:
        raise ValueError(f"SQL 被拒绝或无法执行。{error}") from error
    finally:
        connection.close()


def demo_sql(question):
    """Explicit deterministic intent examples allow offline reproducibility."""
    if re.match(r"^(SELECT|WITH|INSERT|UPDATE|DELETE|DROP|ATTACH|PRAGMA)\b", question, re.IGNORECASE):
        return question
    if "工单" in question or "维护" in question:
        return "SELECT equipment_id, task FROM maintenance WHERE status = 'open' ORDER BY equipment_id"
    if "振动" in question:
        return "SELECT equipment_id, ROUND(MAX(vibration), 2) AS max_vibration FROM measurements GROUP BY equipment_id ORDER BY max_vibration DESC"
    if "温度" in question:
        return "SELECT equipment_id, ROUND(AVG(temperature), 2) AS average_temperature FROM measurements GROUP BY equipment_id ORDER BY average_temperature DESC"
    if "车间" in question or "设备" in question:
        return "SELECT workshop, COUNT(*) AS equipment_count FROM equipment GROUP BY workshop ORDER BY workshop"
    raise ValueError("本地规则支持温度、振动、维护工单或车间设备统计。也可输入 SELECT 查询。")


def run(payload, client):
    question = str(payload.get("text", "")).strip()
    if not question:
        raise ValueError("请输入查询问题。")
    if client.enabled:
        sql = client.complete(
            "你生成 SQLite 只读查询。只输出一个 SELECT 或非递归 WITH 语句，不输出解释和注释。"
            "只使用提供的表和列。禁止修改数据、访问系统表或扩展。所有数据为合成演练。",
            f"数据库结构\n{SCHEMA}\n\n用户问题\n{question}",
            max_tokens=350,
        )
    else:
        sql = demo_sql(question)
    sql = clean_sql(sql)
    result = execute_readonly(sql)
    if client.enabled:
        answer = client.complete(
            "根据 SQL 查询结果简要回答，引用具体结果行。全部记录是合成演练数据。"
            "不要根据相关性推断故障，不足以回答就说明不足。",
            f"问题\n{question}\nSQL\n{sql}\n结果\n{json.dumps(result, ensure_ascii=False)}",
            max_tokens=450,
        )
    else:
        answer = f"本地规则已生成并执行只读 SQL，得到 {len(result['rows'])} 行结果。可在下方核对查询语句和合成数据。"
    return {
        "summary": answer,
        "metrics": {"返回行数": len(result["rows"]), "行数上限": 100, "截断": "是" if result["truncated"] else "否", "执行保护": "SQLite authorizer"},
        "sections": [
            {"title": "实际执行 SQL", "text": sql},
            {"title": "查询结果", "table": {"columns": result["columns"], "rows": result["rows"]}},
            {"title": "数据库结构", "text": SCHEMA},
            {"title": "检查链", "items": ["每次请求创建固定的内存合成数据库", "启用 query_only 和操作/表/函数白名单", "单语句执行，拒绝 SQL 注释与递归操作", "执行时间与指令预算限制，最多返回 100 行"]},
        ],
    }
