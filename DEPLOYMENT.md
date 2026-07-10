# 新媒体工作台部署文档

本文档对应当前仓库中的 React 前端与 FastAPI 后端，适用于本地开发、内部测试和单机部署。

## 1. 系统结构

| 服务 | 技术 | 默认地址 |
| --- | --- | --- |
| 前端 | React + TypeScript + Umi Max + Ant Design | `http://127.0.0.1:5173` |
| 后端 | Python + FastAPI | `http://127.0.0.1:8000` |
| 数据库 | SQLite | `backend/data/content.db` |
| 图片文件 | 本地文件目录 | `backend/uploads/` |

后端进程同时负责定时任务扫描。后端停止时，定时任务不会执行；后端恢复后会继续扫描到期任务。

## 2. 环境要求

- Python 3.11 或更高版本
- Node.js 20 LTS 或更高版本
- npm 10 或更高版本
- Git

检查本机环境：

```bash
python3 --version
node --version
npm --version
git --version
```

## 3. 获取代码

```bash
git clone https://cnb.cool/jason.cnb/hc/ai/AIGC-auto-workflow.git
cd AIGC-auto-workflow
```

已有项目时更新代码：

```bash
git pull origin main
```

## 4. 后端部署

### 4.1 安装依赖

建议使用独立虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r backend/requirements.txt
```

### 4.2 配置模型

复制环境变量模板：

```bash
cp backend/.env.example backend/.env
```

编辑 `backend/.env`，填写实际模型地址、模型名称和密钥：

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=填写文本模型密钥
LLM_MODEL=填写实际可用的模型名称

IMAGE_PROVIDER=doubao
IMAGE_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
IMAGE_API_KEY=填写图片模型密钥
IMAGE_MODEL=doubao-seedream-5-0-260128
```

`backend/.env` 已被 Git 忽略，不要把真实密钥提交到仓库。

### 4.3 启动后端

在项目根目录执行：

```bash
python3 -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

看到以下信息表示启动成功：

```text
Uvicorn running on http://127.0.0.1:8000
```

检查健康状态：

```bash
curl http://127.0.0.1:8000/api/health
```

正常返回：

```json
{"status":"ok"}
```

## 5. 前端部署

### 5.1 安装依赖

```bash
cd frontend
npm install
```

### 5.2 启动开发环境

```bash
env PORT=5173 HOST=127.0.0.1 UMI_APP_API_BASE=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5173
```

浏览器打开：

```text
http://127.0.0.1:5173
```

前端和后端需要同时保持运行。关闭任意一个终端进程，对应服务就会停止。

## 6. 生产环境部署

### 6.1 构建前端

将 `UMI_APP_API_BASE` 替换为生产后端地址：

```bash
cd frontend
env UMI_APP_API_BASE=https://api.example.com npm run build
```

构建结果位于 `frontend/dist/`，可交给 Nginx 或其他静态文件服务托管。

Nginx 单页应用配置示例：

```nginx
server {
    listen 80;
    server_name example.com;
    root /path/to/AIGC-auto-workflow/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

### 6.2 启动生产后端

```bash
python3 -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 --workers 1
```

当前定时任务调度器运行在后端进程内，因此必须使用 `--workers 1`，避免多个进程重复扫描同一任务。生产环境应使用进程管理工具保持后端常驻，并通过 HTTPS 反向代理对外提供服务。

## 7. 数据保存与备份

以下内容不会提交到 Git，需要单独持久化和备份：

- `backend/data/content.db`：项目、草稿、账号、数据分析、定时任务和发布包
- `backend/uploads/`：生成的封面图和正文配图

备份前建议暂停后端，然后复制这两个位置。恢复时放回原目录并重新启动后端。

## 8. 部署后验证

依次验证：

1. 打开 `http://127.0.0.1:8000/api/health`，确认后端正常。
2. 打开 `http://127.0.0.1:5173`，确认前端页面正常。
3. 在“内容创作”生成标题和文章，确认文本模型配置有效。
4. 生成封面图和正文配图，确认图片模型配置有效。
5. 打开草稿箱，确认内容能自动保存和恢复。
6. 创建一个短时间后的单次任务，确认后端保持运行时任务会自动执行。

代码检查命令：

```bash
PYTHONPATH=backend pytest backend/tests -q
python3 -m compileall -q backend/app
cd frontend
npm run typecheck
npm run test
npm run build
```

## 9. 当前功能边界

- 当前仓库中的账号授权是本地联调流程，不等于微信公众号官方授权。
- 真正接入微信公众号需要公众号或开放平台的 AppID、AppSecret、授权回调域名及对应接口权限。
- 视频号、小红书和抖音的官方数据同步与自动发布尚未形成稳定的生产接口。
- 当前数据使用本地 SQLite，适合单机内部使用；多实例和多租户部署时应迁移到服务型数据库。

## 10. 常见问题

### 浏览器显示 `ERR_CONNECTION_REFUSED`

对应服务没有启动。检查端口：

```bash
lsof -nP -iTCP:5173 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

### 后端提示 `address already in use`

说明端口已有进程占用。先用 `lsof` 找到旧进程，确认后停止旧进程，再重新启动当前版本后端。

### 前端提示 `Not Found`

通常是 8000 端口运行了旧版后端。停止旧进程并重新启动当前代码，之后访问 `/api/health` 和目标接口确认路由已加载。

### 前端提示 `Failed to fetch`

确认后端已经启动，并确认前端构建或启动时的 `UMI_APP_API_BASE` 指向正确的后端地址。

### 模型调用失败

检查 `backend/.env` 中的接口地址、模型名称、密钥和账户余额。修改环境变量后必须重新启动后端。
