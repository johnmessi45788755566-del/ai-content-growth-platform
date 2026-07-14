# Task 5 Report: React 真实发布控制

## 实现

- 新增 `PublishAction`、`PublishJobStatus`、`PublishJob` 前端契约，以及发布任务 create/get/retry/resume API 方法。
- Creator 与 Drafts 仅加载 `微信公众号` 且 `已授权` 的账号，每个项目只选择一个账号，并提供“同步到平台草稿箱”和“一键发布”两个独立命令。
- 两个发布入口只根据后端 job 终态展示结果：平台草稿 ID/链接、平台发布 ID/链接、扫码确认说明与恢复按钮、失败阶段/原因与重试按钮。
- Drafts 只有 `restore_stage === 'layout'` 的行显示发布入口；排版前草稿继续保留继续创作和删除。
- Tasks 新增必填“发布方式”，默认 `publish`；创建与编辑分别序列化、恢复 `publish_action`。
- Tasks 列表和详情读取关联 `publish_job`，展示请求动作、job 状态、平台 ID/URL、失败原因和待确认恢复命令；不再展示旧人工接管地址。
- 前端页面和类型未承载 Profile 路径、Cookie、登录 Token、调试端口或发布恢复句柄。

## Review 修复

- Creator 将发布 job 严格绑定到 `project_id`。创建、加载或切换到不同项目时清空旧 job；发布弹窗切换项目时也清空旧 job 与进行中状态。
- Creator 的发布、重试和继续确认异步结果仅在返回 job 仍属于当前项目时写入状态，旧项目的迟到响应不会渲染结果或锁定当前项目账号。
- Tasks 直接渲染列表接口 hydrated 的 `row.publish_job`；继续确认后调用 task 详情接口并重新加载任务列表，确保终态刷新到行内和已打开详情。
- 删除读取源码字符串的 UI 测试，改为 Testing Library + jsdom 的真实组件渲染测试，并 mock API。测试会校验两个按钮的 action 载荷、授权公众号过滤、Creator 跨项目重置、Drafts 排版门槛、retry/resume 调用与终态、Tasks hydrated job 与恢复后刷新。
- 新增独立 Vitest alias 配置与 jsdom 测试环境依赖，不改变生产页面样式或流程。

## TDD 证据

### RED: 载荷与 API 契约

```bash
npm --prefix frontend test -- --run tests/taskPayload.test.ts tests/publishApi.test.ts
```

退出码 `1`。结果为 `2 failed` test files、`4 failed` tests：`publish_action` 尚未序列化，`createPublishJob` 与 `getPublishJob` 尚不存在。

### RED: 页面发布约束

```bash
npm --prefix frontend test -- --run tests/publishingUi.test.ts
```

退出码 `1`。结果为 `1 failed` test file、`4 failed` tests：Creator/Drafts 尚无两种真实发布动作，排版草稿发布门槛和 Tasks 发布任务展示也尚未实现。

### GREEN: 完整前端测试

```bash
npm --prefix frontend test
```

退出码 `0`，精确结果：

```text
Test Files  5 passed (5)
Tests       10 passed (10)
```

Vitest 启动时打印 `WebSocket server error: listen EPERM: operation not permitted 0.0.0.0:24678`；测试仍全部执行并以退出码 `0` 完成。

### Review RED: Creator 跨项目发布状态

```bash
npm --prefix frontend test -- --run tests/publishingUi.test.tsx --reporter=verbose
```

退出码 `1`，结果为 `2 failed | 2 passed`。其中跨项目行为测试明确检测到旧项目的 `平台草稿已创建` 仍在新项目弹窗中渲染；另一个失败为 Ant Select 同时渲染选中项和下拉项导致测试查询不唯一，随后收紧查询。

### Review GREEN: 渲染行为测试

同一命令退出码 `0`，精确结果：

```text
Test Files  1 passed (1)
Tests       4 passed (4)
```

## 用户指定验证

```bash
npm --prefix frontend run typecheck
```

退出码 `0`，精确输出：

```text
> ai-content-growth-frontend@0.1.0 typecheck
> tsc --noEmit
```

```bash
npm --prefix frontend run build
```

退出码 `0`。关键原始输出：

```text
info  - Umi v4.6.73
✔ Webpack: Compiled successfully in 2.27s
info  - Memory Usage: 364.29 MB (RSS: 944.13 MB)
info  - [esbuildHelperChecker] No conflicts found.
225.31 kB  dist/umi.js
8.18 kB    dist/p__Creator.async.js
7.81 kB    dist/p__Tasks.async.js
5.57 kB    dist/p__Drafts.async.js
3.94 kB    dist/umi.css
event - Build index.html
```

`git diff --check` 退出码为 `0`，无输出。

## 范围

- 只修改 Task 5 归属的前端源文件、前端测试和本报告。
- 未编辑 backend；工作区中 Task 4 及其他 worker 的既有改动未回退。
- 保留现有内容创作、草稿恢复/删除、任务 CRUD、账号刷新和任务轮询流程。
