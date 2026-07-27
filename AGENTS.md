# AGENTS.md

## 基础约定

- 默认使用中文沟通，除非用户明确要求其他语言。
- 先理解现有代码和文档，再做改动；避免凭印象重写既有实现。
- 不主动修改业务代码；只有用户明确要求实现、修复或重构时才改动 `backend/app`、`frontend/src`、`frontend/tests`、`backend/tests` 等业务相关文件。
- 涉及删除、覆盖、迁移数据、改 `.env`、执行真实平台发布或外部写入 API 前，必须先确认。
- 不输出密钥、Token、Cookie、浏览器 Profile 路径等敏感信息。

## 项目概览

这是一个新媒体矩阵运营工作台，当前主体为前后端分离应用：

- 前端：React + TypeScript + Umi Max + Ant Design，位于 `frontend/`。
- 后端：Python + FastAPI + SQLite，位于 `backend/`。
- 本地数据：`backend/data/content.db`。
- 本地上传/生成图片：`backend/uploads/`。
- 历史快照：`versions/` 和 `prototypes/`，默认只读参考。
- 产品和设计文档：`docs/`、`DEPLOYMENT.md`、`PROJECT_CONTEXT.md`。

## 常用命令

后端启动：

```bash
python3 -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

后端检查：

```bash
PYTHONPATH=backend pytest backend/tests -q
python3 -m compileall -q backend/app
```

前端启动：

```bash
cd frontend
npm run dev
```

前端检查：

```bash
cd frontend
npm run typecheck
npm run test
npm run build
```

全量前端检查：

```bash
cd frontend
npm run check
```

## 工程边界

- 后端 API 入口在 `backend/app/main.py`。
- 后端核心业务服务在 `backend/app/services.py`。
- SQLite Schema 和兼容迁移在 `backend/app/database.py`。
- 平台发布链路在 `backend/app/publishing/`。
- 前端 API 封装在 `frontend/src/services/api.ts`。
- 前端页面在 `frontend/src/pages/`。
- 前端共享类型在 `frontend/src/types.ts`。

改动时优先沿用已有模块边界，不新增一次性抽象，不做无关重构。

## 发布与账号安全

- 微信公众号发布依赖本地扫码登录和独立 Chrome Profile。
- 发布成功必须有平台回执，不能用本地包或模拟结果冒充成功。
- 对外响应必须持续保持敏感字段脱敏，尤其是浏览器 Profile、Cookie、Token、账号私有字段和内部诊断路径。
- `TRUSTED_FRONTEND_ORIGINS` 必须是明确可信 Origin，不能使用 `*`。
- 生产部署后端当前应保持单 worker，避免内置调度器多进程重复扫描。

## 测试策略

- 改后端接口、任务、发布、账号、数据库迁移时，优先运行相关 `backend/tests`。
- 改前端页面状态、发布弹窗、任务中心、账号授权时，优先运行相关 `frontend/tests`。
- 改跨端契约时，同步检查 `frontend/src/types.ts`、`frontend/src/services/api.ts` 和后端响应字段。
- 文档类改动通常不要求测试，但应确认没有误改业务文件。

## Git 工作区

- 工作区可能已有用户未提交改动；不要回滚或覆盖未确认来源的变更。
- 只提交与当前任务相关的文件。
- 历史版本目录用于追溯，不要在未被要求时更新。
