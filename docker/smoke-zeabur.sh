#!/usr/bin/env bash
# GitHub Actions only: check migrations, all processes and their shared volume.
set -euo pipefail
image=${1:?image is required}
network=dtk-zeabur-smoke

cleanup() {
    result=$?
    if (( result != 0 )); then
        docker logs dtk-zeabur-smoke 2>/dev/null || true
        docker logs dtk-smoke-db 2>/dev/null || true
    fi
    docker rm -f dtk-zeabur-smoke dtk-smoke-db dtk-smoke-redis >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create --internal "$network"
docker run -d --name dtk-smoke-db --network "$network" \
    -e POSTGRES_USER=dtk -e POSTGRES_DB=dtk \
    -e POSTGRES_PASSWORD=smoke-db-password timescale/timescaledb-ha:pg17
docker run -d --name dtk-smoke-redis --network "$network" redis:8-alpine \
    redis-server --requirepass smoke-redis-password
docker run -d --name dtk-zeabur-smoke --network "$network" --read-only \
    --tmpfs /tmp:mode=1777 --tmpfs /data:uid=10001,gid=10001,mode=0700 \
    -e DTK_SECRET_KEY=ci-secret-key-not-used-for-anything-real-0123456789 \
    -e DTK_DATABASE_URL=postgresql+asyncpg://dtk:smoke-db-password@dtk-smoke-db:5432/dtk \
    -e DTK_REDIS_URL=redis://:smoke-redis-password@dtk-smoke-redis:6379/0 \
    -e DTK_DOWNLOADER_TOKEN=smoke-downloader-token "$image"

ready=false
for attempt in $(seq 1 90); do
    if docker exec dtk-zeabur-smoke python -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/readyz", timeout=2).read()'; then
        ready=true
        break
    fi
    sleep 2
done
test "$ready" = true
docker exec dtk-zeabur-smoke /usr/local/bin/downloader healthcheck
docker exec dtk-zeabur-smoke python -c '
import os
from pathlib import Path
assert os.environ["DTK_MEDIA_DIR"] == os.environ["DTK_DOWNLOADER_ROOT"]
path = Path(os.environ["DTK_MEDIA_DIR"]) / "smoke.txt"
path.write_text("shared media")
assert path.read_text() == "shared media"
assert Path(os.environ["DTK_BACKUP_DIR"]).is_dir()
'
# A dead worker must restart the whole service, not leave a healthy-looking API.
docker exec dtk-zeabur-smoke python -c '
import os, signal
from pathlib import Path
for process in Path("/proc").iterdir():
    if not process.name.isdigit():
        continue
    try:
        args = (process / "cmdline").read_bytes().split(b"\0")
    except OSError:
        continue
    if args[1:3] == [b"-m", b"dtk.worker"]:
        os.kill(int(process.name), signal.SIGTERM)
        break
else:
    raise RuntimeError("worker process missing")
'
exit_code=$(timeout 40 docker wait dtk-zeabur-smoke)
test "$exit_code" -eq 1
