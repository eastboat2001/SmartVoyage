"""
功能：封装 MySQL 连接创建逻辑。
作用：为 MCP 服务和编排层提供统一数据库访问入口。
实现方式：从 Config 读取连接参数并返回 mysql.connector 连接对象。
"""

import mysql.connector

from core.config import Config


def get_db_connection(config: Config | None = None):
    conf = config or Config()
    return mysql.connector.connect(
        host=conf.host,
        user=conf.user,
        password=conf.password,
        database=conf.database,
        charset="utf8mb4",
    )
