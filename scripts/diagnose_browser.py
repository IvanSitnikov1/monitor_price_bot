"""Формирует обезличенный отчет о браузерном окружении бота."""

import hashlib
import importlib.metadata
import json
import locale
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _hash_value(value: str) -> str:
    """Возвращает короткий идентификатор без раскрытия исходного значения."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _command_version(command: str) -> str | None:
    """Получает версию исполняемого файла без выполнения пользовательских аргументов."""
    executable = shutil.which(command)
    if not executable:
        return None

    try:
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    return result.stdout.strip() or result.stderr.strip() or None


def _bot_chromium_processes() -> list[dict[str, object]]:
    """Собирает безопасные сведения о Chromium с CDP-портом и профилем бота."""
    processes = []

    for process_dir in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(process_dir.name)
            args = (
                (process_dir / "cmdline").read_bytes()
                .replace(b"\x00", b" ")
                .decode()
                .split()
            )
        except (OSError, UnicodeDecodeError, ValueError):
            continue

        if "--remote-debugging-port=" not in " ".join(args):
            continue
        if any(argument.startswith("--type=") for argument in args):
            continue

        port = next(
            argument.partition("=")[2]
            for argument in args
            if argument.startswith("--remote-debugging-port=")
        )
        profile = next(
            argument.partition("=")[2]
            for argument in args
            if argument.startswith("--user-data-dir=")
        )
        processes.append(
            {
                "pid": pid,
                "port": port,
                "profile_id": _hash_value(profile),
                "executable": Path(args[0]).name if args else None,
            }
        )

    return processes


def _maintenance_flags() -> list[str]:
    """Возвращает типы флагов без раскрытия Telegram ID."""
    flags_dir = ROOT_DIR / "maintenance_flags"
    safe_flags = []
    for path in flags_dir.glob("maintenance_*.flag"):
        match = re.fullmatch(r"maintenance_(\d+)_([a-z]+)\.flag", path.name)
        if match:
            safe_flags.append(f"user_{_hash_value(match.group(1))}_{match.group(2)}")
            continue

        host_match = re.fullmatch(r"maintenance_host_([a-z]+)\.flag", path.name)
        if host_match:
            safe_flags.append(f"host_{host_match.group(1)}")
            continue

        safe_flags.append("unknown")

    return sorted(safe_flags)


def build_report() -> dict[str, object]:
    """Формирует сравнимый отчет без cookies, токенов и полных URL."""
    package_versions = {}
    for package in ("playwright", "playwright-stealth"):
        try:
            package_versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            package_versions[package] = None

    proxy_variables = sorted(
        name
        for name in os.environ
        if "proxy" in name.lower()
    )
    return {
        "created_at": datetime.now().astimezone().isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(aliased=True),
        "kernel": platform.release(),
        "locale": locale.setlocale(locale.LC_ALL, None),
        "chromium": {
            "executable": shutil.which("chromium"),
            "version": _command_version("chromium"),
        },
        "playwright": package_versions,
        "proxy_variable_names": proxy_variables,
        "maintenance_flags": _maintenance_flags(),
        "bot_chromium_processes": _bot_chromium_processes(),
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
