import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


r"""
只启动 6 个后端服务：

.\.venv\Scripts\python.exe run_all.py

连 Streamlit 前端一起启动：
.\.venv\Scripts\python.exe run_all.py --with-ui

连命令行入口一起启动：
.\.venv\Scripts\python.exe run_all.py --with-cli

"""



ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
PYTHON = sys.executable


SERVICES = [
    ("mcp-ticket", [PYTHON, "-u", "mcp_server/mcp_ticket_server.py"]),
    ("mcp-weather", [PYTHON, "-u", "mcp_server/mcp_weather_server.py"]),
    ("mcp-order", [PYTHON, "-u", "mcp_server/mcp_order_server.py"]),
    ("mcp-hotel", [PYTHON, "-u", "mcp_server/mcp_hotel_server.py"]),
    ("a2a-ticket", [PYTHON, "-u", "a2a_server/ticket_server.py"]),
    ("a2a-weather", [PYTHON, "-u", "a2a_server/weather_server.py"]),
    ("a2a-order", [PYTHON, "-u", "a2a_server/order_server.py"]),
    ("a2a-hotel", [PYTHON, "-u", "a2a_server/hotel_server.py"]),
]

UI_SERVICE = ("streamlit", [PYTHON, "-m", "streamlit", "run", "app.py"])
CLI_SERVICE = ("main-cli", [PYTHON, "main.py"])
WATCH_DIRS = [
    ROOT / "a2a_server",
    ROOT / "mcp_server",
    ROOT / "utils",
    ROOT,
]
WATCH_FILE_SUFFIXES = {".py", ".md", ".sql"}

def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def prepare_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for log_file in LOG_DIR.glob("*.log"):
        log_file.write_text("", encoding="utf-8")


def log_path_for(name: str) -> Path:
    if name.startswith("mcp-"):
        return LOG_DIR / "mcp.log"
    if name.startswith("a2a-"):
        return LOG_DIR / "a2a.log"
    return LOG_DIR / f"{name}.log"


def start_process(name: str, command: list[str]) -> tuple[subprocess.Popen, Path]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    log_path = log_path_for(name)
    env["SMARTVOYAGE_LOG_FILE"] = str(LOG_DIR / "app.log")
    log_handle = open(log_path, "a", encoding="utf-8")

    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
        )
    finally:
        log_handle.close()
    return process, log_path


def start_interactive_process(name: str, command: list[str]) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["SMARTVOYAGE_LOG_FILE"] = str(LOG_DIR / "app.log")
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
    )


def run_interactive_process(name: str, command: list[str]) -> int:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["SMARTVOYAGE_LOG_FILE"] = str(LOG_DIR / "app.log")
    print(f"[launcher] entering {name}, press Ctrl+C to stop")
    return subprocess.call(
        command,
        cwd=ROOT,
        env=env,
    )


def snapshot_watch_files() -> dict[Path, int]:
    snapshot: dict[Path, int] = {}
    for watch_dir in WATCH_DIRS:
        if not watch_dir.exists():
            continue
        for path in watch_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in WATCH_FILE_SUFFIXES:
                continue
            try:
                snapshot[path] = path.stat().st_mtime_ns
            except OSError:
                continue
    return snapshot


def diff_watch_files(previous: dict[Path, int], current: dict[Path, int]) -> list[Path]:
    changed: list[Path] = []
    all_paths = set(previous) | set(current)
    for path in sorted(all_paths):
        if previous.get(path) != current.get(path):
            changed.append(path)
    return changed


def restart_process(
    name: str,
    command: list[str],
    process: subprocess.Popen,
) -> tuple[subprocess.Popen, Path]:
    terminate_process(process)
    new_process, log_path = start_process(name, command)
    print(f"[launcher] restarted {name} -> {log_path.relative_to(ROOT)}")
    return new_process, log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Start SmartVoyage services.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--with-ui",
        action="store_true",
        help="Also start the Streamlit frontend.",
    )
    mode_group.add_argument(
        "--with-cli",
        action="store_true",
        help="Also start the command-line entrypoint.",
    )
    parser.add_argument(
        "--startup-delay",
        type=float,
        default=1.0,
        help="Delay in seconds between process starts.",
    )
    parser.add_argument(
        "--dev-reload",
        action="store_true",
        help="Watch project files and automatically restart backend services when code changes.",
    )
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds for --dev-reload.",
    )
    args = parser.parse_args()

    if args.dev_reload and args.with_cli:
        print("[launcher] --dev-reload 暂不支持和 --with-cli 同时使用。")
        return 2

    prepare_log_dir()
    processes: list[tuple[str, subprocess.Popen, Path]] = []
    service_processes: dict[str, tuple[subprocess.Popen, Path]] = {}
    service_commands = {name: command for name, command in SERVICES}

    try:
        for name, command in SERVICES:
            process, log_path = start_process(name, command)
            processes.append((name, process, log_path))
            service_processes[name] = (process, log_path)
            print(f"[launcher] started {name} -> {log_path.relative_to(ROOT)}")
            time.sleep(args.startup_delay)

        if args.with_ui:
            process = start_interactive_process(*UI_SERVICE)
            processes.append((UI_SERVICE[0], process, ROOT / "CONSOLE"))
            print("[launcher] started streamlit in current terminal")

        print("[launcher] all requested processes started, press Ctrl+C to stop")
        print("[launcher] mcp service output -> logs/mcp.log")
        print("[launcher] a2a service output -> logs/a2a.log")
        print("[launcher] application logger -> logs/app.log")
        if args.dev_reload:
            print("[launcher] dev reload enabled")

        if args.with_cli:
            return run_interactive_process(*CLI_SERVICE)

        watch_snapshot = snapshot_watch_files() if args.dev_reload else {}
        while True:
            if args.dev_reload:
                current_snapshot = snapshot_watch_files()
                changed_files = diff_watch_files(watch_snapshot, current_snapshot)
                if changed_files:
                    display_files = ", ".join(str(path.relative_to(ROOT)) for path in changed_files[:5])
                    if len(changed_files) > 5:
                        display_files += ", ..."
                    print(f"[launcher] detected changes: {display_files}")
                    restarted_services: list[str] = []
                    for name in service_commands:
                        old_process, _ = service_processes[name]
                        new_process, log_path = restart_process(name, service_commands[name], old_process)
                        service_processes[name] = (new_process, log_path)
                        restarted_services.append(name)
                    processes = [
                        (name, service_processes[name][0], service_processes[name][1])
                        if name in service_processes
                        else (name, proc, log_path)
                        for name, proc, log_path in processes
                    ]
                    print(f"[launcher] restarted services: {', '.join(restarted_services)}")
                    watch_snapshot = current_snapshot
                    time.sleep(args.startup_delay)
                    continue

            failed = [
                (name, proc.returncode, log_path)
                for name, proc, log_path in processes
                if proc.poll() is not None
            ]
            if failed:
                for name, returncode, log_path in failed:
                    if log_path.name == "CONSOLE":
                        print(f"[launcher] {name} exited with code {returncode}.")
                    else:
                        print(
                            f"[launcher] {name} exited with code {returncode}. "
                            f"See {log_path.relative_to(ROOT)} for details."
                        )
                return 1
            time.sleep(args.watch_interval if args.dev_reload else 1)

    except KeyboardInterrupt:
        print("[launcher] stopping processes")
        return 0
    finally:
        for _, process, _ in reversed(processes):
            terminate_process(process)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if os.name == "nt":
        signal.signal(signal.SIGINT, signal.default_int_handler)
    raise SystemExit(main())
