"""为已有数据库补建新增字段。

`Base.metadata.create_all` 只补建缺失表，不会修改已存在的表；本模块以幂等 DDL
补齐新增列，使升级后无需清空演示数据卷。
"""

from sqlalchemy import inspect, text

from src.config.database import engine

_ADDED_COLUMNS = {
    'runs': {'relabel_sheet_id': 'INTEGER REFERENCES relabel_sheets(id)'},
    'schedules': {'relabel_sheet_id': 'INTEGER REFERENCES relabel_sheets(id)'},
}


def ensure_schema():
    with engine.begin() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table, columns in _ADDED_COLUMNS.items():
            if table not in tables:
                continue
            present = {column['name'] for column in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {ddl}'))
