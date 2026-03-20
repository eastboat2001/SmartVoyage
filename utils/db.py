import mysql.connector

from config import Config


def get_db_connection(config: Config | None = None):
    conf = config or Config()
    return mysql.connector.connect(
        host=conf.host,
        user=conf.user,
        password=conf.password,
        database=conf.database,
        charset="utf8mb4",
    )
