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

## 独立审查修复（2026-07-14）

### 修复内容

- Creator 与 Drafts 的发布、重试、恢复、刷新均绑定 `project_id + request generation`。关闭弹窗、切换项目或切换草稿会使旧请求失效；旧请求的成功、失败和 `finally` 都不能覆盖或解除新请求状态。
- Creator 与 Drafts 的 publish job 轮询允许最多 2 次连续查询失败。达到上限后保留当前 job ID 和状态，显示“刷新发布状态”；刷新到 `awaiting_confirmation` 后继续保留扫码说明与恢复入口。
- Tasks 对 hydrated `publish_job.status === 'failed'` 在列表与详情提供“重试发布任务”，调用 `retryPublishJob(job.id)` 后读取任务详情并刷新列表，不调用整任务 `retryTasks`。
- V1 任务表单仅允许“微信公众号图文生成”与已授权微信公众号账号；其他任务类型作为禁用预留项显示。旧非微信任务仍保留列表、详情和删除能力，编辑弹窗明确提示 V1 未开放并禁用提交。
- `buildTaskPayload` 拒绝非微信公众号或非微信任务类型，避免把预留平台任务与微信账号组合后错误序列化。

### 行为测试 RED / GREEN

RED 命令：

```bash
npm --prefix frontend test -- --run tests/publishingUi.test.tsx tests/taskPayload.test.ts --reporter=verbose
```

退出码 `1`，结果为 `2 failed` test files、`6 failed | 5 passed` tests。失败分别命中 Creator 迟到错误泄漏、Drafts 跨草稿锁定、轮询首次失败即退出、Tasks 缺少 job 级重试、旧非微信任务仍可提交，以及非微信 payload 未拒绝。

GREEN 定向命令退出码 `0`：

```text
Test Files  2 passed (2)
Tests       11 passed (11)
```

### 最终验证

`npm --prefix frontend test` 退出码 `0`：

```text
Test Files  5 passed (5)
Tests       15 passed (15)
```

`npm --prefix frontend run typecheck` 退出码 `0`：

```text
> tsc --noEmit
```

`npm --prefix frontend run build` 退出码 `0`，关键输出：

```text
✔ Webpack: Compiled successfully in 2.44s
info  - Memory Usage: 362.27 MB (RSS: 928.48 MB)
info  - [esbuildHelperChecker] No conflicts found.
225.31 kB  dist/umi.js
8.81 kB    dist/p__Creator.async.js
8.18 kB    dist/p__Tasks.async.js
6.23 kB    dist/p__Drafts.async.js
event - Build index.html
```

`git diff --check` 退出码 `0`，无输出。

### Concerns

- Vitest 仍打印 Vite CJS API deprecated 警告及 HMR WebSocket `listen EPERM 0.0.0.0:24678`；不影响测试执行，最终退出码为 `0`。
- Drafts 的乱序响应测试中 Ant Design 打印 `Warning: There may be circular references`；断言与测试退出码均正常，生产 build 无对应警告。
- 本次仅修改 Task 5 前端页面、payload 工具、前端测试和本报告；未修改 backend、README、PRD、账号矩阵或数据分析文件。

## 第三轮前端修复（2026-07-14）

### 修复内容

- Creator 与 Drafts 的 `beginPublishRequest` 在生成新请求代次时统一清空 `publishingAction` 和 `refreshingPublishJob`。refresh 未完成时启动 retry/resume，新操作失败后刷新按钮会恢复可点击；旧请求的响应和 `finally` 仍受项目/草稿与 generation 校验，不会覆盖新代次状态。
- Tasks 的 failed job retry 行为测试不再采用 `retryPublishJob` 返回值模拟最终 UI。测试明确断言 retry 后依次调用任务详情与任务列表接口，并只按两次刷新返回的 task/job 渲染最终发布 ID、清除旧失败原因。
- `PlatformAccount` 删除非公开 `login_url` 字段，并增加编译期字段白名单测试；发布页面使用的账号类型不声明 `profile_path`、`login_url`、token 或浏览器恢复上下文。网络响应裁剪由并行后端修复负责。

### TDD 证据

移除 loading 修复后运行：

```bash
npm --prefix frontend test -- --run tests/publishingUi.test.tsx -t "releases refresh loading" --reporter=verbose
```

退出码 `1`，精确结果为 `2 failed | 8 skipped`。Creator 的 refresh -> retry 与 Drafts 的 refresh -> resume 均因刷新按钮仍包含 `ant-btn-loading` 失败。

公开字段白名单 RED：

```bash
npm --prefix frontend run typecheck
```

退出码 `2`，`tests/accountTypes.test.ts` 报错 `Type 'true' is not assignable to type 'false'`，命中 `PlatformAccount.login_url` 超出白名单。

恢复最小修复后的竞态 GREEN：

```bash
npm --prefix frontend test -- --run tests/publishingUi.test.tsx -t "releases refresh loading" --reporter=verbose
```

退出码 `0`：

```text
Test Files  1 passed (1)
Tests       2 passed | 8 skipped (10)
```

### 最终验证

`npm --prefix frontend test` 退出码 `0`：

```text
Test Files  6 passed (6)
Tests       18 passed (18)
```

`npm --prefix frontend run typecheck` 退出码 `0`：

```text
> tsc --noEmit
```

`npm --prefix frontend run build` 退出码 `0`，关键输出：

```text
✔ Webpack: Compiled successfully in 2.48s
info  - Memory Usage: 362.25 MB (RSS: 896.72 MB)
info  - [esbuildHelperChecker] No conflicts found.
225.31 kB  dist/umi.js
8.81 kB    dist/p__Creator.async.js
8.18 kB    dist/p__Tasks.async.js
6.23 kB    dist/p__Drafts.async.js
event - Build index.html
```

`git diff --check` 退出码 `0`，无输出。

### Concerns

- Vitest 继续打印 Vite CJS API deprecated 与 HMR WebSocket `listen EPERM 0.0.0.0:24678`；完整测试仍全部执行并以退出码 `0` 结束。
- Drafts 既有乱序响应测试继续打印 Ant Design `Warning: There may be circular references`；断言通过，生产 build 无对应警告。
- 本轮未修改 Tasks 生产实现：现有实现已经在 retry 后读取任务详情并刷新任务列表，本轮只让测试严格验证调用顺序与刷新接口事实。
- 本轮只修改 Task 5 的 Creator、Drafts、类型、前端测试与报告；未触碰 backend、README、PRD、Accounts、Analytics 或 `global.css`。
