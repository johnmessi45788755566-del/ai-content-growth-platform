# Task 3 Report: 发布协调器与 API

## 实现

- 新增 `PublishCoordinator`，统一创建、查询、重试和恢复发布任务。
- 发布调用前持久化 `pending` 与 `checking_login`，调用后持久化平台结果或失败阶段。
- 仅在草稿/发布 ID、真实微信 URL 和非空平台响应同时存在时写入成功状态。
- `awaiting_confirmation` 的 `job_context` 仅保存在数据库内部；API 递归移除恢复句柄、Profile、session、token、WebSocket 等浏览器上下文。
- publisher 异常中的账号 Profile 路径在持久化和资源返回时均脱敏。
- 新增发布任务 create/get/retry/resume HTTP 端点；无效输入、账号或平台返回 4xx，publisher 失败返回 HTTP 200 的 `failed` job。
- 旧 `publish-package` 端点通过 `draft` job 执行，并同步 status、平台草稿 ID/URL、平台响应和失败字段；失败 package 同样作为 HTTP 200 资源返回。

## TDD 证据

### RED 1: 发布任务路由

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py -k 'publish_job' -q
```

结果：退出码 `1`，`4 failed, 17 deselected`；新端点均返回预期的 `404 Not Found`。

### RED 2: 浏览器上下文脱敏

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py::test_failed_publish_job_does_not_expose_browser_profile_path -q
```

结果：退出码 `1`，`1 failed`；失败资源原样包含 `/tmp/sensitive-wechat-profile`。

### RED 3: 同项目账号绑定

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py::test_create_publish_job_rejects_switching_project_account -q
```

结果：退出码 `1`，`1 failed`；第二个账号触发 SQLite `publish_jobs project_id must use one account_id`，尚未转换为 API 400。

### GREEN

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py -k 'publish_job or publish_package' -q
PYTHONPATH=backend pytest backend/tests/test_content_flow.py::test_publish_coordinator_persists_transition_before_external_publish -q
PYTHONPATH=backend pytest backend/tests -q
python -m compileall -q backend/app backend/tests
git diff --check -- backend/app/publishing/coordinator.py backend/app/services.py backend/app/main.py backend/tests/test_content_flow.py backend/tests/test_api.py
```

最终聚焦 API 测试结果为 `9 passed, 15 deselected, 1 warning`，协调器状态测试为 `1 passed`。完整后端测试结果为 `110 passed, 1 warning`；唯一 warning 是既有 FastAPI TestClient 的 Starlette/httpx 弃用提示。编译与差异检查均为退出码 `0`。

## 范围

- 修改仅限 Task 3 所有权文件。
- 未修改 `browser_gateway.py`、`wechat.py`、`browser_auth.py`、`database.py` 或 frontend。
- 工作区中其他 worker 的账号、分析、Creator、Tasks 等既有脏改动未回退。

## Review 修复

### 实现

- 草稿成功要求 `source=wechat_platform`、`event=draft_created`、`receipt_id` 等于草稿 ID，且 `corroboration` 为 `draft_saved` 或 `draft_api_response`。
- 直接发布成功要求 `event=article_published`、匹配的发布 ID，以及 `publish_success` 或 `publish_api_response`；任一字段不匹配均落为失败资源。
- retry 使用 `WHERE status='failed'`、resume 使用 `WHERE status='awaiting_confirmation'` 的条件 UPDATE 原子 claim，并检查 `rowcount`；竞争失败者不调用 publisher，也不写入结果。
- resume 上下文在 claim 后、执行 `try` 内解析；上下文缺失或损坏时持久化为 `failed`，HTTP 返回 200 资源。
- 失败文本持久化前统一清理 URL query/fragment，并不区分大小写地脱敏 token、code、state、resume、CDP、WebSocket、Profile 和调试端口；失败时同时清除内部浏览器上下文。
- 兼容 package 失败响应保持 HTTP 200，且与关联 job 的状态、失败阶段、失败原因、项目和账号一致。

### Review TDD 证据

RED 命令：

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py -k 'weak_or_action or expose_browser or damaged_resume or publish_package_failure' -q
PYTHONPATH=backend pytest backend/tests/test_content_flow.py -k 'claim_allows_only_one' -q
```

RED 结果：API 为 `3 failed, 1 passed, 23 deselected`；并发 claim 为 `2 failed, 19 deselected`。失败分别证明弱回执被误判成功、敏感浏览器信息被持久化、损坏 resume 返回 400，以及并发 retry/resume 均调用 publisher 两次。兼容 package 失败合同在新增测试下已满足。

最终 GREEN：

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py -k 'publish_job or publish_package or damaged_resume' -q
# 12 passed, 15 deselected, 1 warning

PYTHONPATH=backend pytest backend/tests/test_content_flow.py -k 'publish_coordinator or claim_allows_only_one' -q
# 3 passed, 18 deselected

PYTHONPATH=backend pytest backend/tests -q
# 115 passed, 1 warning

python -m compileall -q backend/app backend/tests
git diff --check -- backend/app/publishing/coordinator.py backend/app/services.py backend/app/main.py backend/tests/test_content_flow.py backend/tests/test_api.py .superpowers/sdd/task-3-report.md
# exit code 0
```

唯一 warning 仍为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示。
