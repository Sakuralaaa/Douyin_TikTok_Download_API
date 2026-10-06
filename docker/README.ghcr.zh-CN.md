# 此 fork 的 GitHub Actions 镜像

`Publish GHCR images` 工作流在 `main` / `release` 的部署相关代码变更、`v*` 标签和手动运行时构建镜像。PR 只构建和验证，不发布。手动运行可以取消 `publish`，只验证。

| 镜像 | 用途 |
|---|---|
| `ghcr.io/sakuralaaa/douyin_tiktok_download_api` | API、worker、数据库迁移；用启动参数区分 |
| `ghcr.io/sakuralaaa/douyin_tiktok_download_api-browser` | browser-rpc，包含固定版本的 CloakBrowser 和 Chromium |
| `ghcr.io/sakuralaaa/douyin_tiktok_download_api-downloader` | 媒体下载服务 |

三个镜像都包含 `linux/amd64`、`linux/arm64`。使用 GitHub 原生 AMD64 / ARM64 runner 构建和启动验证。所有六项构建与验证成功后，才发布多架构标签。主应用检查包导入、控制台资源和启动参数；浏览器检查 RPC 健康状态并在无外网、只读根目录下实际启动 Chromium；下载器检查服务健康。

标签包括 `latest`、分支名、`sha-<完整提交 SHA>`。推送 `v5.1.4` 之类的版本标签还会生成 `5.1.4`、`5.1`。部署时建议固定 `sha-...` 或镜像 digest。

工作流用 `GITHUB_TOKEN` 写入 GHCR，无需 Docker Hub 密钥。GHCR 新包首次创建可能是 private；若要让 Zeabur 无凭据拉取，到 GitHub 个人主页的 Packages 中，把这三个包的 Package settings → Change visibility 改为 Public。镜像的 source 标签会关联当前 fork。

浏览器 pin 存在 `docker/browser-version.env`：CloakHQ/cloakbrowser v0.5.12，commit `1c867a7046effc5f0324941e4569006d05411344`。构建下载该版本自带的免费 Chromium（AMD64 146.0.7680.177.5、ARM64 146.0.7680.177.3），无需额外浏览器许可证。自动更新默认关闭，升级时修改 pin 并重新运行工作流。

## Docker Compose 使用预构建镜像

按上游文档先生成仓库根目录 `.env`，再使用覆盖文件：

```bash
docker compose --env-file .env -p dtk \
  -f docker/compose.yml -f docker/compose.ghcr.yml \
  --profile browser --profile downloader pull

docker compose --env-file .env -p dtk \
  -f docker/compose.yml -f docker/compose.ghcr.yml \
  --profile browser --profile downloader up -d --no-build --wait
```

覆盖文件要求 Docker Compose 2.24.4+，移除了所有应用服务的本机构建。默认使用 `latest`，可通过 shell 的 `DTK_GHCR_TAG=sha-...` 固定版本。`.env` 中还需要：

```dotenv
DTK_BROWSER_RPC_URL=http://browser-rpc:9000
DTK_DOWNLOADER_URL=http://downloader:9100
```

下载器的共享密钥 `DTK_DOWNLOADER_TOKEN` 按上游文档生成，并在应用和下载器两侧保持一致。密钥、Cookie、代理凭据只保存在部署环境，不能提交到仓库。

## Zeabur / Oracle ARM

在绑定 Oracle 服务器的 Zeabur 项目中部署镜像服务，不运行宿主机一键安装脚本。主镜像的 API、worker、migrate 分别传入 `api`、`worker`、`migrate` 参数；后两者不需要公开域名。

数据库必须是带 TimescaleDB 扩展的 PostgreSQL 17（例如 `timescale/timescaledb-ha:pg17`），Redis 使用 8。先确保数据库和 Redis 就绪，再执行迁移，成功后启动 API 和 worker。浏览器和下载器只提供内部地址，分别监听 9000、9100；API 监听 8000，通过 Zeabur 绑定 HTTPS 域名。

持久化数据库、Redis、媒体和备份；API 与下载器需要看到同一份媒体文件，API 与 worker 需要共享备份。Zeabur 每个服务各建一个独立卷并不会自动共享文件，部署时需要选择可共享的存储或调整服务组合。
