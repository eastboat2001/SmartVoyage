import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

import mysql.connector


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from config import Config

CREATE_SQL = ROOT / "sql" / "create_table.sql"
SEED_SQL = ROOT / "sql" / "insert_data.sql"
SOFT_RESET_TABLE_ORDER = [
    "orders",
    "hotel_room_inventory",
    "hotels",
    "flight_tickets",
    "train_tickets",
    "weather_data",
    "user_preferences",
    "users",
]

SERVICE_MARKERS = [
    "run_all.py" ,
    "main.py" ,
    "app.py" ,
    "mcp_server/mcp_ticket_server.py" ,
    "mcp_server/mcp_weather_server.py" ,
    "mcp_server/mcp_order_server.py" ,
    "mcp_server/mcp_hotel_server.py" ,
    "a2a_server/ticket_server.py" ,
    "a2a_server/weather_server.py" ,
    "a2a_server/order_server.py" ,
    "a2a_server/hotel_server.py" ,
    "-m streamlit run app.py" ,
]


def _strip_line_comment(line: str) -> str:
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == '-' and not in_single and not in_double and i + 1 < len(line) and line[i + 1] == '-':
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def parse_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    in_single = False
    in_double = False

    for raw_line in sql_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith('--'):
            continue
        line = _strip_line_comment(raw_line)
        if not line.strip():
            continue

        current: list[str] = []
        i = 0
        while i < len(line):
            ch = line[i]
            current.append(ch)
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == ';' and not in_single and not in_double:
                buffer.append(''.join(current))
                statement = '\n'.join(part.rstrip() for part in buffer).strip()
                if statement:
                    statements.append(statement[:-1].strip())
                buffer = []
                current = []
            i += 1

        if current:
            buffer.append(''.join(current).rstrip())

    trailing = '\n'.join(part.rstrip() for part in buffer).strip()
    if trailing:
        statements.append(trailing)
    return statements


def load_sql_file(path: Path) -> list[str]:
    return parse_sql_statements(path.read_text(encoding='utf-8'))


def _summarize_statement(statement: str) -> str:
    first_line = next((line.strip() for line in statement.splitlines() if line.strip()), '')
    return first_line[:120]


def _list_windows_processes() -> list[dict]:
    result = subprocess.run(
        [
            'powershell',
            '-NoProfile',
            '-Command',
            'Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress',
        ],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=True,
    )
    payload = result.stdout.strip()
    if not payload:
        return []
    data = json.loads(payload)
    if isinstance(data, dict):
        return [data]
    return [item for item in data if isinstance(item, dict)]


def stop_smartvoyage_services(verbose: bool = True, wait_seconds: float = 1.5) -> None:
    if os.name != 'nt':
        if verbose:
            print('[reset-db] auto-stop services is currently implemented for Windows only; skipping', flush=True)
        return

    root_lower = str(ROOT).lower().replace('\\', '/')
    current_pid = os.getpid()
    target_pids: dict[int, str] = {}

    try:
        processes = _list_windows_processes()
    except Exception as exc:
        if verbose:
            print(f'[reset-db] failed to inspect running processes: {exc}', flush=True)
        return

    for process in processes:
        pid = int(process.get('ProcessId') or 0)
        if not pid or pid == current_pid:
            continue
        command_line = str(process.get('CommandLine') or '')
        normalized = command_line.lower().replace('\\', '/')
        if not normalized or root_lower not in normalized:
            continue
        matched_marker = next((marker for marker in SERVICE_MARKERS if marker in normalized), '')
        if matched_marker:
            target_pids[pid] = matched_marker

    if not target_pids:
        if verbose:
            print('[reset-db] no SmartVoyage service processes detected', flush=True)
        return

    if verbose:
        print(f'[reset-db] stopping {len(target_pids)} SmartVoyage service process(es)', flush=True)

    for pid, marker in sorted(target_pids.items()):
        if verbose:
            print(f'[reset-db] stopping pid={pid} ({marker})', flush=True)
        subprocess.run(
            ['taskkill', '/PID', str(pid), '/T', '/F'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )

    if wait_seconds > 0:
        time.sleep(wait_seconds)


def execute_statements(cursor, statements: Iterable[str], verbose: bool = False, phase: str = '') -> None:
    statements = list(statements)
    total = len(statements)
    for index, statement in enumerate(statements, start=1):
        if not statement:
            continue
        if verbose:
            prefix = f'[reset-db] {phase} ' if phase else '[reset-db] '
            print(f"{prefix}statement {index}/{total}: {_summarize_statement(statement)}", flush=True)
        cursor.execute(statement)


def _existing_tables(cursor, database: str) -> set[str]:
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
        """,
        (database,),
    )
    return {str(row[0]) for row in cursor.fetchall()}


def _soft_reset_cleanup_statements(cursor, database: str) -> list[str]:
    existing_tables = _existing_tables(cursor, database)
    statements = ['SET FOREIGN_KEY_CHECKS = 0']
    for table in SOFT_RESET_TABLE_ORDER:
        if table in existing_tables:
            statements.append(f'DELETE FROM {table}')
    statements.append('SET FOREIGN_KEY_CHECKS = 1')
    return statements


def _seed_statements_for_existing_database(seed_sql: Path) -> list[str]:
    return load_sql_file(seed_sql)


def _soft_reset_database(conf: Config, seed_sql: Path, verbose: bool = True) -> None:
    db_conn = mysql.connector.connect(
        host=conf.host,
        user=conf.user,
        password=conf.password,
        database=conf.database,
        charset='utf8mb4',
    )
    try:
        db_conn.autocommit = True
        cursor = db_conn.cursor()
        try:
            cursor.execute('SET SESSION lock_wait_timeout = 10')
            cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
            if verbose:
                print(
                    '[reset-db] services are kept alive; using soft reset mode '
                    'to avoid DROP DATABASE metadata locks',
                    flush=True,
                )
                print(f'[reset-db] clearing existing data in: {conf.database}', flush=True)
            execute_statements(
                cursor,
                _soft_reset_cleanup_statements(cursor, conf.database),
                verbose=verbose,
                phase='cleanup',
            )
            if verbose:
                print(f'[reset-db] applying seed data: {seed_sql}', flush=True)
            execute_statements(
                cursor,
                _seed_statements_for_existing_database(seed_sql),
                verbose=verbose,
                phase='seed',
            )
        finally:
            cursor.close()
    finally:
        db_conn.close()


def reset_database(
    create_sql: Path = CREATE_SQL,
    seed_sql: Path = SEED_SQL,
    verbose: bool = True,
    stop_services_first: bool = True,
    stop_wait_seconds: float = 1.5,
) -> None:
    if stop_services_first:
        stop_smartvoyage_services(verbose=verbose, wait_seconds=stop_wait_seconds)

    conf = Config()
    if not stop_services_first:
        _soft_reset_database(conf, seed_sql=seed_sql, verbose=verbose)
        if verbose:
            print(f'[reset-db] database reset completed: {conf.database}', flush=True)
        return

    root_conn = mysql.connector.connect(
        host=conf.host,
        user=conf.user,
        password=conf.password,
        charset='utf8mb4',
    )
    try:
        root_conn.autocommit = True
        cursor = root_conn.cursor()
        try:
            cursor.execute('SET SESSION lock_wait_timeout = 10')
            cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
            if verbose:
                print(f'[reset-db] applying schema: {create_sql}', flush=True)
            execute_statements(cursor, load_sql_file(create_sql), verbose=verbose, phase='schema')
        finally:
            cursor.close()
    finally:
        root_conn.close()

    db_conn = mysql.connector.connect(
        host=conf.host,
        user=conf.user,
        password=conf.password,
        database=conf.database,
        charset='utf8mb4',
    )
    try:
        db_conn.autocommit = True
        cursor = db_conn.cursor()
        try:
            cursor.execute('SET SESSION lock_wait_timeout = 10')
            cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
            if verbose:
                print(f'[reset-db] applying seed data: {seed_sql}', flush=True)
            execute_statements(cursor, load_sql_file(seed_sql), verbose=verbose, phase='seed')
        finally:
            cursor.close()
    finally:
        db_conn.close()

    if verbose:
        print(f'[reset-db] database reset completed: {conf.database}', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description='Reset SmartVoyage database to baseline data.')
    parser.add_argument('--create-sql', default=str(CREATE_SQL), help='Path to schema SQL file')
    parser.add_argument('--seed-sql', default=str(SEED_SQL), help='Path to seed SQL file')
    parser.add_argument('--quiet', action='store_true', help='Suppress progress output')
    parser.add_argument('--skip-stop-services', action='store_true', help='Do not stop SmartVoyage services before resetting the database')
    parser.add_argument('--stop-wait-seconds', type=float, default=1.5, help='Seconds to wait after stopping SmartVoyage services')
    args = parser.parse_args()

    reset_database(
        create_sql=Path(args.create_sql),
        seed_sql=Path(args.seed_sql),
        verbose=not args.quiet,
        stop_services_first=not args.skip_stop_services,
        stop_wait_seconds=args.stop_wait_seconds,
    )


if __name__ == '__main__':
    main()
