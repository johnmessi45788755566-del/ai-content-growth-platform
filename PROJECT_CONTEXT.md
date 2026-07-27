# PROJECT_CONTEXT.md

更新日期：2026-07-22

## 项目定位

本项目是“新媒体矩阵运营工作台”，面向内容创作、账号矩阵管理、草稿沉淀、定时任务和平台发布。当前可运行形态是本地/单机前后端应用：前端提供运营工作台界面，后端负责 AI 内容生成、图片生成、SQLite 持久化、账号扫码会话、发布任务和调度恢复。

## 当前结构

```text
.
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── main.py          # API 入口、CORS、调度器生命周期
│   │   ├── services.py      # 内容、草稿、账号、任务、发布编排
│   │   ├── database.py      # SQLite Schema 与兼容迁移
│   │   ├── ai.py            # OpenAI-compatible 文本/图片客户端
│   │   ├── browser_auth.py  # 平台扫码登录与浏览器会话管理
│   │   └── publishing/      # 平台发布适配、任务协调、浏览器网关
│   ├── tests/               # 后端测试
│   ├── data/                # 本地数据库和浏览器 Profile，Git 忽略
│   ├── uploads/             # 生成/上传图片，Git 忽略
│   └── README.md
├── frontend/                # React + Umi Max + Ant Design 前端
│   ├── src/pages/           # Home、Creator、Drafts、Accounts、Tasks 等页面
│   ├── src/services/api.ts  # 后端 API 封装
│   ├── src/types.ts         # 前后端共享契约类型
│   └── tests/               # 前端单元和 UI 状态测试
├── docs/                    # PRD、方案和设计文档
├── prototypes/              # 早期原型
├── versions/                # 历史快照/备份
├── DEPLOYMENT.md            # 本地和单机部署说明
├── AGENTS.md                # 协作规则
└── CHANGELOG.md             # 变更记录
```

## 技术栈

- 前端：React 18、TypeScript、Umi Max、Ant Design、Vitest、Testing Library。
- 后端：Python、FastAPI、Pydantic、SQLite、pytest、websocket-client。
- AI 接入：文本模型走 OpenAI-compatible 接口；图片模型配置中支持火山 Ark / Doubao。
- 浏览器自动化：通过本地 Chrome Profile 保存平台登录态，发布链路使用浏览器网关操作平台页面。

## 核心业务能力

- AI 选题推荐。
- 公众号文章生成。
- 封面图和正文配图生成。
- 内容阶段流转：标题、文章、图片、排版、发布。
- 草稿自动保存、恢复、删除。
- 账号矩阵授权、状态同步和指标分析。
- 任务中心：单次/重复任务、失败重试、任务详情、定时扫描。
- 微信公众号真实平台草稿和直接发布，发布过程保留平台回执与失败原因。

## 当前开发状态

- 当前稳定产品边界仍以微信公众号 V1 发布链路为主。
- 代码中已经出现平台发布路由和小红书发布模块，说明多平台发布正在推进或已部分落地；合并前需要确认前后端契约和产品边界是否同步。
- 数据库 Schema 已包含内容项目、文章块、媒体资源、排版块、草稿快照、平台账号、发布包、发布任务、账号指标、作品指标、定时任务和任务运行记录。
- 后端内置调度器每 30 秒执行维护逻辑：恢复待确认、恢复发布任务、同步到期账号、执行到期任务。
- 前端页面包括首页、内容创作、草稿箱、账号矩阵、任务中心、数据分析和设置。

## 当前工作区快照

截至本次上下文治理校验，工作区已有未提交业务变更：

```text
M  backend/app/database.py
M  backend/app/main.py
M  backend/app/publishing/__init__.py
M  backend/app/publishing/coordinator.py
M  backend/app/services.py
M  backend/tests/test_content_flow.py
M  docs/prd/新媒体矩阵运营工作台PRD.md
M  frontend/src/pages/Tasks.tsx
M  frontend/src/types.ts
M  frontend/src/utils/taskPayload.ts
M  frontend/tests/taskPayload.test.ts
?? backend/app/publishing/router.py
?? backend/app/publishing/xiaohongshu.py
```

这些变更不是本次文档治理创建的；后续处理时应先确认其作者、目的和是否需要一起提交。

## 运行方式

后端：

```bash
python3 -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
python3 -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

默认地址：

- 后端：`http://127.0.0.1:8000`
- 前端：`http://127.0.0.1:5173`

## 验证入口

后端：

```bash
PYTHONPATH=backend pytest backend/tests -q
python3 -m compileall -q backend/app
```

前端：

```bash
cd frontend
npm run typecheck
npm run test
npm run build
```

## 高风险区域

- 发布任务幂等、重试、恢复、待人工确认和终态同步。
- 平台真实回执校验，不能接受弱证据或历史回执。
- 账号身份稳定性校验，不能用同名账号合并不同真实身份。
- 公开 API 响应脱敏，尤其是嵌套错误信息和浏览器上下文。
- 定时任务并发认领、重复任务换代和失败恢复。
- 前端发布弹窗、草稿箱和任务中心的异步请求竞争。
- SQLite 兼容迁移和历史数据回填。

## 本地数据与忽略文件

以下路径属于本地运行产物，默认不提交：

- `backend/data/*.db`
- `backend/data/browser_profiles/`
- `backend/logs/`
- `backend/uploads/`
- `frontend/dist/`
- `frontend/src/.umi/`
- `frontend/src/.umi-production/`
- `.pnpm-store/`
- `.pytest_cache/`

## 接手建议

- 做业务改动前先看 `git status --short`，确认是否有他人未提交代码。
- 涉及发布链路时先跑目标测试，再改代码；该区域并发和恢复语义复杂。
- 涉及多平台发布时，同步检查数据库约束、后端公开响应、前端类型和页面文案。
- 文档、PRD、部署说明和测试描述应保持同一产品边界，避免“代码支持”和“产品开放范围”不一致。
