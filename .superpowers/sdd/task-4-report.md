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
