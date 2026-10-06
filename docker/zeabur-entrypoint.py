"""Run the three storage-sharing processes as one Zeabur service."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import asyncpg
from redis.asyncio import Redis

from dtk.core.config import BootstrapSettings

stopping = False
children: dict[str, subprocess.Popen] = {}


def log(message: str) -> None:
    print(f"dtk-zeabur: {message}", flush=True)


def request_stop(_signum: int, _frame: object) -> None:
    global stopping
    stopping = True


def spawn(name: str, command: list[str]) -> subprocess.Popen:
    process = subprocess.Popen(command, start_new_session=True)
    children[name] = process
    log(f"started {name} pid={process.pid}")
    return process


async def wait_for_stores(settings: BootstrapSettings) -> None:
    deadline = time.monotonic() + 300
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)
    try:
        while not stopping and time.monotonic() < deadline:
            connection = None
            try:
                connection = await asyncpg.connect(
                    settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1),
                    timeout=5,
                )
                await connection.execute("SELECT 1")
                await redis.ping()
                log("database and Redis are ready")
                return
            except (OSError, asyncpg.PostgresError, asyncio.TimeoutError):
                log("waiting for database and Redis")
            except Exception as exc:
                # Connection errors may contain credentials; log only the type.
                log(f"waiting for database and Redis ({type(exc).__name__})")
            finally:
                if connection is not None:
                    await connection.close()
            await asyncio.sleep(3)
    finally:
        await redis.aclose()
    raise RuntimeError("dependencies did not become ready within the startup budget")


def shutdown() -> None:
    for process in children.values():
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 20
    for process in children.values():
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def main() -> int:
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        settings = BootstrapSettings()
        for directory in (settings.backup_dir, os.environ["DTK_MEDIA_DIR"]):
            Path(directory).mkdir(parents=True, exist_ok=True)
        asyncio.run(wait_for_stores(settings))
        migration = spawn("migrate", ["/usr/local/bin/dtk-entrypoint", "migrate"])
        while migration.poll() is None and not stopping:
            time.sleep(0.2)
        if stopping:
            return 0
        if migration.returncode != 0:
            log(f"migration failed with exit code {migration.returncode}")
            return 1
        del children["migrate"]
        log("migration completed")
        spawn("downloader", ["/usr/local/bin/downloader"])
        spawn("worker", ["/usr/local/bin/dtk-entrypoint", "worker"])
        spawn("api", ["/usr/local/bin/dtk-entrypoint", "api"])
        while not stopping:
            for name, process in children.items():
                if process.poll() is not None:
                    log(f"{name} exited with code {process.returncode}; stopping the service")
                    return 1
            time.sleep(0.5)
        return 0
    except Exception as exc:
        log(f"startup failed ({type(exc).__name__})")
        return 1
    finally:
        shutdown()


if __name__ == "__main__":
    sys.exit(main())
