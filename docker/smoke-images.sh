#!/usr/bin/env bash
# Run on GitHub's native Linux runners, never as part of a local setup.
set -euo pipefail

kind=${1:?image kind is required}
image=${2:?image name is required}
container="dtk-smoke-${kind}"

cleanup() {
    result=$?
    if (( result != 0 )); then
        docker logs "$container" 2>/dev/null || true
    fi
    docker rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

case "$kind" in
    app)
        docker run --rm --network none --read-only --tmpfs /tmp:mode=1777 \
            --entrypoint sh "$image" -c '
                set -eu
                dtk --version
                test -s "$DTK_CONSOLE_DIR/index.html"
                python -c "import dtk.api.app, dtk.worker, dtk.db.migrate"
            '
        docker run --rm --network none "$image" help
        result=0
        docker run --rm --network none "$image" api || result=$?
        test "$result" -eq 78
        result=0
        docker run --rm --network none "$image" unknown-role || result=$?
        test "$result" -eq 64
        ;;
    browser)
        docker run -d --name "$container" --network none --read-only --init \
            --tmpfs /tmp:size=1g,mode=1777 \
            --tmpfs /profiles:size=128m,uid=10001,gid=10001,mode=0700 \
            --shm-size=1g --memory=4g --pids-limit=1024 \
            -e DTK_BROWSER_PREWARM=false \
            -e DTK_BROWSER_GEO_PROBE_URL= \
            -e CLOAKBROWSER_AUTO_UPDATE=false "$image"
        ready=false
        for attempt in $(seq 1 30); do
            if docker exec "$container" python -c '
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:9000/rpc/health", timeout=2) as response:
    health = json.load(response)
assert health["status"] == "ok", health
assert health["backend"] == "cloak", health
'; then
                ready=true
                break
            fi
            sleep 2
        done
        test "$ready" = true
        # Exercise the actual runtime UID, read-only root, and cache links.
        docker exec -i "$container" python - <<'PY'
import asyncio
from cloakbrowser import binary_info, launch_persistent_context_async

assert binary_info()["installed"], binary_info()

async def main():
    context = await launch_persistent_context_async(
        user_data_dir="/profiles/smoke", headless=True, geoip=False
    )
    try:
        page = await context.new_page()
        await page.goto("data:text/html,<title>dtk-smoke</title>")
        assert await page.title() == "dtk-smoke"
        print("CloakBrowser launched successfully")
    finally:
        await context.close()

asyncio.run(main())
PY
        ;;
    downloader)
        docker run -d --name "$container" --network none --read-only \
            --tmpfs /var/lib/dtk/media:uid=10001,gid=10001,mode=0700 "$image"
        ready=false
        for attempt in $(seq 1 15); do
            if docker exec "$container" /usr/local/bin/downloader healthcheck; then
                ready=true
                break
            fi
            sleep 1
        done
        test "$ready" = true
        ;;
    *)
        echo "Unknown image kind: $kind" >&2
        exit 64
        ;;
esac
