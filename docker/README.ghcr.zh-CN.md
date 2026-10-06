# 此 fork 的 GitHub Actions 镜像

`Publish GHCR images` 工作流在 `main` / `release` 的部署相关代码变更、`v*` 标签和手动运行时构建镜像。PR 只构建和验证，不发布。手动运行可以取消 `publish`，只验证。

| 镜像 | 用途 |
|---|---|
| `ghcr.io/sakuralaaa/douyin_tiktok_download_api` | API、worker、数据库迁移；用启动参数区分 |
| `ghcr.io/sakuralaaa/douyin_tiktok_download_api-browser` | browser-rpc，包含固定版本的 CloakBrowser 和 Chromium |
| `ghcr.io/sakuralaaa/douyin_tiktok_download_api-downloader` | 媒体下载服务 |
| `ghcr.io/sakuralaaa/douyin_tiktok_download_api-zeabur` | Zeabur 专用：API、worker、下载器共用一个持久化卷 |

四个镜像都包含 `linux/amd64`、`linux/arm64`。使用 GitHub 原生 AMD64 / ARM64 runner 构建和启动验证。三个基础镜像的六项构建与验证全部成功后，才发布多架构标签。主应用检查包导入、控制台资源和启动参数；浏览器检查 RPC 健康状态并在无外网、只读根目录下实际启动 Chromium；下载器检查服务健康。Zeabur 专用镜像的验证见下文。

标签包括 `latest`、分支名、`sha-<完整提交 SHA>`。推送 `v5.1.4` 之类的版本标签还会生成 `5.1.4`、`5.1`。部署时建议固定 `sha-...` 或镜像 digest。

工作流用 `GITHUB_TOKEN` 写入 GHCR，无需 Docker Hub 密钥。GHCR 新包首次创建可能是 private；若要让 Zeabur 无凭据拉取，到 GitHub 个人主页的 Packages 中，把需要部署的包的 Package settings → Change visibility 改为 Public。镜像的 source 标签会关联当前 fork。

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

使用本仓库的 [`zeabur-template.yml`](zeabur-template.yml)，在绑定 Oracle 服务器的 Zeabur 项目中部署四个镜像服务：TimescaleDB、Redis、browser-rpc 和 dtk。模板使用已通过 GitHub Actions 验证的固定提交镜像，不需要在本地或 Oracle 宿主机编译。

数据库使用带 TimescaleDB 扩展的 PostgreSQL 17，Redis 使用 8。`Publish Zeabur image` 工作流在基础镜像发布成功后构建专用镜像，并在 AMD64、ARM64 上验证数据库迁移、API 就绪、下载器、共享目录和 worker 故障后的整体退出。运行时，入口程序等待数据库和 Redis 可用，执行迁移，然后启动 API、worker 和下载器。

dtk 的 `/data` 持久化卷同时保存媒体和备份，三个进程共享这些目录。Zeabur 的初始化容器与主容器继承同一 UID，因此模板先用 root 初始化目录，再立即切换到 UID/GID 10001 启动入口程序；API、worker、下载器均以普通用户运行。下载器只监听 `127.0.0.1:9100`。浏览器使用内网 9000 端口；数据库、Redis 和浏览器不绑定公网域名、不开放外部端口。仅 API 的 8000 端口绑定 Zeabur HTTPS 域名。

部署时生成四个独立随机密钥，对应模板的 `DTK_MASTER_KEY`、`DTK_DB_PASSWORD`、`DTK_CACHE_PASSWORD`、`DTK_DOWNLOAD_KEY`。主密钥用于加密数据，升级时必须保留。密钥保存在 Zeabur 变量中，不写入 Git。

服务启动后，从 dtk 日志取出 `/setup?token=...`，把日志中的 `http://127.0.0.1:8000` 替换为实际 HTTPS 地址，打开页面设置管理员账号。初始化令牌有效期为 24 小时，完成初始化后失效。`/readyz` 可检查数据库和 Redis 是否就绪；身份池中的 Cookie、代理和平台风控情况需要在控制台另行配置。

升级前备份数据库和 `/data`，在 Zeabur 保留现有服务和卷，仅更新 dtk、browser-rpc 的镜像标签到已验证的同一提交。自动构建镜像不会自动更新正在运行的服务。模板中的镜像标签必须是具体 Docker 引用，Zeabur 不接受在 `source.image` 中用模板变量拼接标签。

## 代理探测与铸造

代理列表中没有“最近探测”时间的记录显示“未知”；导入时的默认启用状态不能证明连通性。只有实际探测结果才显示“健康”或“不可用”。探测通过代表可访问 GeoIP 检测站点，不保证抖音或 TikTok 会下发有效身份 Cookie。

应用启用 `httpx[socks]`，支持 SOCKS 代理探测。浏览器会把 `socks5h` 转换为 Chromium 使用的 `socks5` 名称，仍在代理端解析目标域名，并保留账号密码。GitHub 镜像验证包含真实 Chromium 通过本地带密码的 SOCKS 代理访问测试站点，验证身份认证和代理端 DNS。
