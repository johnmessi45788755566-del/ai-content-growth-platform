# Task 4 Report: 定时任务接入真实发布协调器

## 实现

- 任务创建与更新合同新增 `publish_action`，仅接受 `publish` / `draft`，默认 `publish`；列表、详情和持久化均返回该字段。
- `scheduled_tasks` 与 `task_runs` 新增 `publish_job_id` 外键，并通过幂等 `ALTER TABLE` 迁移已有数据库。
- 定时任务完成标题、文章、图片和排版后，以已授权微信公众号 `account_id` 和任务动作调用 `create_publish_job`。
- 任务状态以关联发布作业为准：
  - `published` -> `已发布`
  - `platform_draft` -> `已生成平台草稿`
  - `awaiting_confirmation` -> `待人工确认`
  - `failed` -> `失败`，复制协调器已脱敏的失败阶段和原因
- 直接发布与平台草稿保持独立；兼容 `publish_package` 仅镜像作业结果，不再决定任务真相。
- 任务详情和 `GET /api/tasks` 列表行均返回协调器生成的公开 `publish_job`，人工确认句柄等私有响应上下文不会进入任务响应。
- `resume_publish_job` 会同步关联的等待任务和运行记录；重复任务只在最终平台成功后计算下一次时间。
- 创建、更新和执行均拒绝非微信公众号或未授权账号；V1 定时任务真实发布仅支持微信公众号。
- 到期执行使用条件 UPDATE 原子领取任务；失败重试同样用 `status = '失败'` 条件领取，避免并发调度或重试重复生成。

## TDD 证据

### RED 1: 合同、状态映射与恢复

```bash
python -m pytest -q tests/test_api.py -k 'task_publish_action or direct_publish or distinct_from_direct or waits_for_confirmation or platform_failure_copies'
```

结果：退出码 `1`，`5 failed, 26 deselected`。失败证明动作更新未持久化、默认任务仍被当作草稿、详情缺少 `publish_job`、等待确认被当作失败，且失败阶段仍来自旧草稿路径。

### RED 2: 迁移与并发执行

```bash
python -m pytest -q tests/test_content_flow.py -k 'existing_scheduled_tasks or concurrent_task_execution'
```

结果：退出码 `1`，`2 failed, 21 deselected`。旧库缺少 `publish_job_id`，两个并发执行调用触发了两次 publisher。

### RED 3: 并发失败重试

```bash
python -m pytest -q tests/test_content_flow.py::test_concurrent_failed_task_retries_claim_once
```

结果：退出码 `1`，`1 failed`；两个并发重试均返回 `updated = 1`。

### RED 4: 任务列表发布作业水合

```bash
python -m pytest -q tests/test_api.py::test_task_list_hydrates_linked_public_publish_job
```

结果：退出码 `1`，`1 failed, 1 warning`；列表行已有 `publish_job_id`，但读取 `publish_job` 时触发 `KeyError`。

## 最终验证

### 聚焦任务测试

```bash
python -m pytest -q tests/test_api.py tests/test_content_flow.py::test_existing_scheduled_tasks_reject_invalid_publish_action_after_migration tests/test_content_flow.py::test_concurrent_task_execution_claims_generation_once tests/test_content_flow.py::test_concurrent_failed_task_retries_claim_once -k task
```

结果：退出码 `0`，`20 passed, 16 deselected, 1 warning in 1.45s`。

### 完整后端测试

```bash
python -m pytest -q
```

结果：退出码 `0`，`124 passed, 1 warning in 3.81s`。

唯一 warning 为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示。

### 编译检查

```bash
python -m compileall -q app tests
```

结果：无输出，退出码 `0`。

## 范围与安全

- 修改仅限 `backend/app/database.py`、`backend/app/services.py`、`backend/app/main.py`、`backend/tests/test_api.py`、`backend/tests/test_content_flow.py` 和本报告。
- 未修改 frontend、`backend/app/publishing/*` 或浏览器发布适配器文件。
- 所有任务发布测试均使用 fake publisher；未执行任何真实平台写入。
- 工作区中其他协作者的既有脏改动未回退。

## 自审

- 发布作业是任务状态、平台证据和恢复状态的唯一事实来源；兼容 package 只保留旧调用方需要的字段。
- 任务列表通过与详情相同的公开协调器资源水合关联作业，可直接显示真实状态、平台 ID、失败信息和确认动作。
- 重复任务处于 `待人工确认` 时 `next_run_at` 为空，run 不写 `finished_at`；恢复成功后才完成 run 并排下次执行。
- 失败阶段和原因来自公开作业资源，因此沿用协调器的浏览器上下文脱敏。

## 审查修复

- `publish_packages` 新增显式 `publish_job_id`，旧数据从 `payload_json.publish_job_id` 幂等回填。
- 协调器返回 job 后立即在事务中关联 task/run；package、作品或 autosave 后处理失败时重新读取 job，以平台成功事实终结任务，不进入再次发布路径。
- 失败任务若已关联 job，会重试或复用同一 job；重复任务进入下一周期时才清空上一代关联并创建新 job。
- task、run、package 的最终状态在同一事务内提交；恢复写入使用 task 状态、job ID、run ID 和 run 状态 CAS，陈旧恢复不再覆盖新一代。
- 创建和更新先比较请求平台与账号平台，再执行 V1 微信限制；非微信请求不会被账号静默改写。
- `execute_due_tasks` 返回实际成功 claim 数；任务详情和列表公开 job 测试显式排除 `resume_context`、`token`、`credentials`、`job_context`。

### RED: 审查问题复现

```bash
python -m pytest -q tests/test_api.py -k 'platform_account_mismatch or local_postprocessing_failure or stale_resume or roll_back_together or reports_only_successful_claims'
```

结果：退出码 `1`，`5 failed, 33 deselected, 1 warning in 0.79s`。分别复现平台静默改写、平台成功被本地失败覆盖、陈旧恢复覆盖新代次、分裂提交和 claim 虚报。

```bash
python -m pytest -q tests/test_content_flow.py::test_existing_publish_package_backfills_publish_job_id_from_payload tests/test_content_flow.py::test_concurrent_failed_task_retries_claim_once
```

结果：退出码 `1`，`2 failed in 0.24s`。旧 package 未回填 job ID，失败重试调用平台后任务仍停留在 `执行中`。

```bash
python -m pytest -q tests/test_api.py::test_confirmation_resume_recovers_when_local_package_was_not_created
```

结果：退出码 `1`，`1 failed, 1 warning in 0.38s`。无本地 package 的等待确认任务恢复时对空 ID 执行 `int(None)`。

### GREEN: 审查覆盖测试

```bash
python -m pytest -q tests/test_api.py -k 'platform_account_mismatch or local_postprocessing_failure or stale_resume or package_was_not_created or roll_back_together or reports_only_successful_claims or starts_next_generation or task_list_hydrates or waits_for_confirmation'
```

结果：退出码 `0`，`9 passed, 31 deselected, 1 warning in 0.93s`。

```bash
python -m pytest -q tests/test_content_flow.py::test_existing_publish_package_backfills_publish_job_id_from_payload tests/test_content_flow.py::test_concurrent_failed_task_retries_claim_once
```

结果：退出码 `0`，`2 passed in 0.21s`。

### 最终验证

```bash
python -m pytest -q tests/test_api.py
```

结果：退出码 `0`，`40 passed, 1 warning in 2.50s`。

```bash
python -m pytest -q
```

结果：退出码 `0`，`132 passed, 1 warning in 4.13s`。

```bash
python -m compileall -q app tests
```

结果：无输出，退出码 `0`。warning 仍为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示。

### Concern

- 平台已成功但本地后处理失败时，任务会忠实显示平台成功且不会重发；对应作品、autosave 或兼容 package 仍可能缺失，需要后续独立的本地补偿机制修复展示数据，但不能再次调用平台发布。

## 收尾复核

- 复核发现人工确认恢复还有同类窗口：平台恢复成功后，若 task/run/package 本地同步事务失败，job 已进入终态，后续恢复会被协调器拒绝。
- 服务层现在仅在终态 job 仍关联“待人工确认”任务时重做本地同步；不会再次调用平台。没有待同步任务的终态 job 仍保持原有非法恢复语义。

### RED: 平台成功后恢复本地同步

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py::test_resume_retries_local_sync_without_republishing_after_platform_success -q
```

结果：退出码 `1`，`1 failed, 1 warning in 0.38s`。第一次恢复已使平台 job 成功，但本地事务被故障注入回滚；第二次恢复因 job 不再是 `awaiting_confirmation` 被拒绝。

### GREEN: 恢复与并发聚焦测试

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py::test_resume_retries_local_sync_without_republishing_after_platform_success -q
```

结果：退出码 `0`，`1 passed, 1 warning in 0.36s`；两次本地同步尝试只调用了一次平台 resume。

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py -q -k 'waits_for_confirmation or stale_resume or package_was_not_created or roll_back_together or retries_local_sync'
```

结果：退出码 `0`，`5 passed, 36 deselected, 1 warning in 0.69s`。

```bash
PYTHONPATH=backend pytest backend/tests/test_content_flow.py::test_resume_publish_job_claim_allows_only_one_publisher_call -q
```

结果：退出码 `0`，`1 passed in 0.17s`。

### 最终指定验证

```bash
PYTHONPATH=backend pytest backend/tests -q
```

结果：退出码 `0`，`133 passed, 1 warning in 3.34s`。

```bash
python3 -m compileall -q backend/app
```

结果：无输出，退出码 `0`。

```bash
git diff --check
```

结果：无输出，退出码 `0`。

唯一 warning 仍为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示。

## 第二轮后端审查修复

### 修复内容

- 公开 `publish_job` 继续递归过滤规范化敏感键，新增覆盖 `resume_context`/`resumeContext`、`credentials`/`credential`、`api_key`/`apiKey`、`access_token`/`accessToken`、`refresh_token`、cookie、token、secret、profile path 和 debug port；普通平台回执字段保持可见。
- 迁移先从 `publish_packages.payload_json.publish_job_id` 回填 package，再沿 `publish_package_id` 回填 `scheduled_tasks.publish_job_id` 和 `task_runs.publish_job_id`；所有更新仅处理空值，可重复执行。
- 首次任务执行先创建 pending job 与 `pending_publish` package，并在平台调用前原子关联 task/run/package/job；平台终态后，task、run、package 只在同一事务中最终化。进程中断或本地同步失败可读取同一 job 恢复，不再次调用平台。
- task 关联失败 job 的 retry API 在平台返回 published、awaiting confirmation 或 failed 后，使用 job ID、task/run 旧状态和 run ID CAS 原子同步 task、run、package；重复任务仅在最终成功后计算下一次执行时间。终态 job 遇到本地事务失败时，后续 retry 只重做本地同步。
- `/api/accounts`、账号详情、授权完成、更新及同步响应统一使用显式公开白名单；浏览器授权、检测和同步内部仍按 account ID 读取完整账号记录，不向网络响应返回 profile/login/QR、sync error 或浏览器会话字段。

### RED: 实际故障注入

- 嵌套发布响应实际注入全部敏感键后，列表、详情和协调器资源测试共 `2 failed`，确认 `resume_context` 等规范化键会进入网络响应。
- 旧数据仅在 package payload 中保存 job ID 时，迁移测试失败，确认 task/run 未沿 package 关联回填。
- 平台调用窗口、最终化事务回滚和进程中断恢复测试共 `3 failed`，分别确认平台调用前缺少 package 关联、分裂终态写入和中断后无法恢复。
- task 关联 job 的 retry API 三种终态及陈旧代次测试共 `4 failed`，确认 job 已变化而 task/run/package 仍停留旧状态或可被旧 job 覆盖。
- 账号表实际写入 profile path、带 token/cookie/debug port 的 login URL、credentials 和 sync error 后，公开接口白名单测试失败，确认私有列原样出现在响应中。

### GREEN: 聚焦回归

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py backend/tests/test_content_flow.py -q
```

结果：退出码 `0`，`78 passed, 1 warning in 4.45s`。覆盖秘密实际注入与普通回执保留、迁移幂等和旧失败任务复用、首次执行窗口/回滚/中断恢复、retry 三终态/本地同步重试/陈旧代次 CAS、resume CAS、待确认重复排期和账号公开白名单。

### 最终验证

```bash
PYTHONPATH=backend pytest backend/tests -q
```

结果：退出码 `0`，`145 passed, 1 warning in 4.59s`。

```bash
python3 -m compileall -q backend/app
```

结果：无输出，退出码 `0`。

```bash
git diff --check
```

结果：无输出，退出码 `0`。唯一 warning 仍为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示。

### 第二轮 Concern

- 平台 job 终态仍先于本地 task/run/package 事务提交，这是外部系统与 SQLite 无法组成分布式事务的固有限制；当前通过预建 pending 本地记录、终态 package 不提前暴露、同 job 幂等恢复和 CAS 防陈旧覆盖控制风险。
- 本轮所有发布路径测试使用 fake publisher 或故障注入，未执行任何真实平台写入。
