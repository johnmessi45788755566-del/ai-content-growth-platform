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

- 修改仅限 `backend/app/database.py`、`backend/app/publishing/coordinator.py`、`backend/app/services.py`、`backend/app/main.py`、`backend/tests/test_api.py`、`backend/tests/test_content_flow.py` 和本报告。
- 未修改 frontend 或浏览器发布适配器文件；后续审查按允许范围修改了 `backend/app/publishing/coordinator.py`。
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

## 最终后端复审修复

### 修复内容

- `PATCH /api/tasks/{id}` 现在以单条条件更新同时检查 task 状态和关联 publish job 状态；`执行中`、`待人工确认` 或关联 pending/checking/uploading/filling/publishing/awaiting job 的任务返回 `409`，不会清空 project/package/job 关联。
- publish job 公开资源在键规范化后匹配敏感语义片段，覆盖 client secret、API token、cookie jar、profile dir、debug info 以及 resume、credential、secret、token、cookie、profile、debug、password、auth、session、port 变体；普通 platform draft ID、publish ID、URL 和 receipt 保持可见。
- 平台 job 已终态后，task/run/package 最终化失败不再调用 `_finish_task_failure`。task/run 保持 `执行中`、package 保持 `pending_publish`，恢复扫描仅重试本地事务；连续本地失败被隔离，不会重新调用 publisher。

### RED: 最终复审故障复现

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py::test_patch_task_is_rejected_while_publish_is_running_without_duplicate_job backend/tests/test_api.py::test_patch_task_is_rejected_while_waiting_or_linked_job_is_active backend/tests/test_api.py::test_public_publish_job_drops_nested_normalized_secret_keys backend/tests/test_api.py::test_task_list_and_detail_drop_nested_publish_job_secrets backend/tests/test_api.py::test_initial_task_finalization_rollback_stays_active_until_local_recovery backend/tests/test_api.py::test_repeated_local_finalization_failures_remain_pending_without_republishing -q
```

结果：退出码 `1`，`6 failed, 1 warning in 0.96s`。分别复现活动任务 PATCH 成功清空关联、敏感语义键变体泄漏，以及终态 job 本地最终化失败后 task/run 被错误标记失败。

### GREEN: 最终复审聚焦回归

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py backend/tests/test_content_flow.py -q
```

结果：退出码 `0`，`81 passed, 1 warning in 4.41s`。并发阻塞发布测试确认 PATCH 返回 `409`、publisher 调用和 job 数均为 `1`；待确认与异常状态但仍关联活动 job 的任务同样不可编辑。故障注入还确认一次最终化失败可由扫描恢复，连续失败保持 pending 且不重发。

### 最终指定验证

```bash
PYTHONPATH=backend pytest backend/tests -q
```

结果：退出码 `0`，`148 passed, 1 warning in 4.62s`。

```bash
python3 -m compileall -q backend/app
```

结果：无输出，退出码 `0`。

```bash
git diff --check
```

结果：无输出，退出码 `0`。唯一 warning 仍为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示；所有平台发布测试均使用 fake publisher，未执行真实平台写入。

## Task 4 后端复审完整 findings 收口

### 修复内容

- PATCH 在 `BEGIN IMMEDIATE` 事务内同时检查 task、最新 run、package 和关联 job。活动、失败可重试、待确认以及平台已终态但本地记录未同步的 job 均返回 `409`；只有 task/run/package 已完整同步的成功终态允许重新编辑。
- `scheduled_tasks.generation` 作为调度代次 CAS。due 扫描和失败重试都携带读取时 generation，PATCH 与领取都会递增；失败重试的状态标记和领取合并为一次原子 UPDATE，旧扫描不能执行被延期或改代的任务。
- 公开 publish job 顶层改为显式字段白名单；嵌套响应按 snake/camel/紧凑键 token 边界过滤，覆盖 storage state、verification code、private key、client secret、API token、cookie jar、profile dir、debug info 及 resume/auth/session/port 族，同时保留 `report_id`、`author_name`、平台 ID、URL 和普通 receipt。可疑裸字符串直接替换为隐藏占位符。
- 旧 package payload 仅在 `publish_job_id` 的 JSON 类型为 integer 且目标 `publish_jobs` 行存在时回填。账号级联删除 job 后再次初始化数据库不会恢复悬空引用。
- 平台 job 已终态后的本地 task/run/package 最终化恢复路径保持不变：扫描或 resume 只重试本地事务，不再次调用 publisher。

### RED: 完整 findings 复现

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py::test_public_publish_job_drops_nested_normalized_secret_keys backend/tests/test_api.py::test_due_scan_cannot_claim_task_after_patch_moves_it_to_future backend/tests/test_api.py::test_retry_scan_cannot_claim_task_after_patch_changes_generation backend/tests/test_api.py::test_patch_task_rejects_unsynchronized_terminal_publish_job backend/tests/test_content_flow.py::test_publish_package_backfill_ignores_deleted_or_non_integer_job_ids -q
```

结果：退出码 `1`，`6 failed, 1 warning in 0.83s`。分别复现 token 子串误删 `report_id`、due/retry 陈旧快照继续领取、published/platform draft 关联被 PATCH 清空，以及级联删除 job 后迁移触发外键错误。

紧凑无分隔敏感键追加 RED：`test_public_publish_job_drops_nested_normalized_secret_keys` 失败，`storagestate`、`verificationcode`、`privatekey` 字段仍出现在公开响应；并发 linked retry 对照测试通过。

### GREEN: 聚焦回归

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py::test_public_publish_job_drops_nested_normalized_secret_keys backend/tests/test_api.py::test_due_scan_cannot_claim_task_after_patch_moves_it_to_future backend/tests/test_api.py::test_retry_scan_cannot_claim_task_after_patch_changes_generation backend/tests/test_api.py::test_linked_job_retry_and_patch_keep_same_generation_and_job backend/tests/test_api.py::test_patch_task_rejects_unsynchronized_terminal_publish_job backend/tests/test_api.py::test_initial_task_finalization_rollback_stays_active_until_local_recovery backend/tests/test_api.py::test_repeated_local_finalization_failures_remain_pending_without_republishing backend/tests/test_api.py::test_resume_retries_local_sync_without_republishing_after_platform_success backend/tests/test_content_flow.py::test_publish_package_backfill_ignores_deleted_or_non_integer_job_ids -q
```

结果：退出码 `0`，`10 passed, 1 warning in 0.76s`。linked retry 与 PATCH 交错严格保持 `409`、同一 job、publisher 总调用 `2`（首次失败加原位重试）和 job 总数 `1`；due/retry 陈旧代次均为 `0` 次发布、`0` 个 job。

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py backend/tests/test_content_flow.py -q
```

结果：退出码 `0`，`87 passed, 1 warning in 5.36s`。

### 最终完整验证

```bash
PYTHONPATH=backend pytest backend/tests -q
```

结果：退出码 `0`，`154 passed, 1 warning in 4.74s`。

```bash
python3 -m compileall -q backend/app
```

结果：无输出，退出码 `0`。

```bash
git diff --check
```

结果：无输出，退出码 `0`。唯一 warning 仍为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示。

### Concern

- 平台终态与 SQLite 本地最终化仍无法组成单一分布式事务；当前以持久 job、同 job 恢复、generation CAS 和本地终态重放保证不重发。所有发布测试均使用 fake publisher，未执行真实平台写入。
- generation 由服务层支持的 PATCH、due 与 retry 路径维护；绕过服务直接更新 `scheduled_tasks` 的外部 SQL 不在并发保证范围内。

## 最新复审剩余阻断项修复

### 修复内容

- publish job 顶层继续使用固定公开字段，`response` 改为严格结构化投影：仅保留平台证据和普通业务回执字段；`receipt` / `platform_receipt` 也只保留显式普通字段。`job_context`、`_attempt_token`、任意未知键和未知嵌套结构不会进入公开资源，保留字符串仍统一执行 URL 查询参数和凭证文本脱敏。
- 移除依赖不断补充敏感键名的递归 denylist。测试实际注入 `sessiontoken`、`cookieheader`、`clientsecretvalue`、`privatekeypem`、`portnumber` 及对应裸字符串，同时验证 `source`、`event`、`receipt_id`、`title`、`corroboration`、`confirmation`、平台 ID、URL、report/author 和普通 receipt 字段保留。
- 新增事务内历史关联解析：统一比较 task、最新 run、task/run 所指 package 的非空 `publish_job_id`。唯一关联会按需回填 task 和最新 run；出现多个不同 job 或悬空 job 时 fail closed。
- 旧库迁移可沿最新 run 或最新 run 的 package 回填 task job；原有 payload JSON 类型及目标 job 存在性检查保持不变。
- PATCH、失败 retry、手动 execute 和恢复扫描都经过同一历史关联解析。task 链接被清空但 run/package 仍保留终态 job 时，PATCH 返回 `409`，retry/scan 只同步同一 job，不创建新 job、不重复调用 publisher。重复任务仅在上一代 task/run/package 已完整同步成功时清理旧关联并进入新 generation。

### TDD 证据

公开响应白名单 RED：

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py::test_public_publish_response_recursively_redacts_sensitive_string_values backend/tests/test_api.py::test_public_publish_job_drops_nested_normalized_secret_keys backend/tests/test_api.py::test_task_list_and_detail_drop_nested_publish_job_secrets -q
```

结果：退出码 `1`，`3 failed, 1 warning in 0.60s`。未知顶层、未知嵌套和新命名字段仍进入 API。

历史关联 RED：

```bash
PYTHONPATH=backend pytest backend/tests/test_content_flow.py::test_existing_task_backfills_publish_job_from_latest_run_or_package backend/tests/test_api.py::test_patch_rejects_publish_job_retained_only_in_history backend/tests/test_api.py::test_retry_recovers_terminal_job_retained_only_in_history backend/tests/test_api.py::test_scan_recovers_terminal_job_retained_only_in_history -q
```

结果：退出码 `1`，`8 failed, 1 warning in 1.03s`。迁移未回填，PATCH 错误放行，retry 新建 job 2，scan 停留在执行中。

### GREEN 与最终验证

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py::test_public_publish_response_recursively_redacts_sensitive_string_values backend/tests/test_api.py::test_public_publish_job_drops_nested_normalized_secret_keys backend/tests/test_api.py::test_task_list_and_detail_drop_nested_publish_job_secrets -q
```

结果：退出码 `0`，`3 passed, 1 warning in 0.46s`。

```bash
PYTHONPATH=backend pytest backend/tests/test_content_flow.py::test_existing_task_backfills_publish_job_from_latest_run_or_package backend/tests/test_api.py::test_patch_rejects_publish_job_retained_only_in_history backend/tests/test_api.py::test_retry_recovers_terminal_job_retained_only_in_history backend/tests/test_api.py::test_scan_recovers_terminal_job_retained_only_in_history backend/tests/test_api.py::test_conflicting_task_run_and_package_jobs_fail_closed -q
```

结果：退出码 `0`，`9 passed, 1 warning in 0.92s`。run-only/package-only 均恢复同一 job；冲突关联 fail closed；publisher 调用与 job 数未增加。

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py backend/tests/test_content_flow.py -q
```

结果：退出码 `0`，`96 passed, 1 warning in 6.86s`。

```bash
PYTHONPATH=backend pytest backend/tests -q
```

结果：退出码 `0`，`163 passed, 1 warning in 5.23s`。

```bash
python3 -m compileall -q backend/app backend/tests
```

结果：无输出，退出码 `0`。

```bash
git diff --check
```

结果：无输出，退出码 `0`。唯一 warning 仍为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示；所有发布测试使用 fake publisher，未执行真实平台写入。

## 最新两个 Important findings 修复：代次隔离与发布中断恢复

### 修复内容

- `task_runs` 新增 `generation INTEGER NOT NULL DEFAULT 0`，已有库通过幂等 `ALTER TABLE` 迁移；旧 task/run 均保持可解析的 `generation=0`，已有非零代次库将最新 legacy run 对齐到 task 当前代次。
- claim 在 resolver 之前先核验状态与 generation，陈旧 due/retry 快照会直接退出，不提交任何历史 job 回填。成功 claim 将 `scheduled_tasks.generation + 1` 的确切值写入新 run。
- resolver 只读取 task 当前 generation 的最新 run，以及 task/current run 指向的 package。once 成功后 PATCH 可清空旧代关联并执行新主题；repeat 下一次运行同样使用新 run generation 和新 publish job。
- task/run/job/package 已关联且 job 为 `pending` 时，扫描和手动 execute 都通过 publish job CAS 执行同一个 job，不创建新 job。
- `checking_login`、`uploading_assets`、`filling_content`、`publishing` 不自动重发。超过固定 15 分钟无更新时，原 job CAS 为 `failed`，`failure_stage=发布中断`，reason 明确要求人工核对平台后再决定重试，并沿终态本地恢复同步 task/run/package；近期活动 job 保持不变。
- SQLite `CURRENT_TIMESTAMP` 使用 UTC，超时边界会把调度本地时间显式转换为 UTC，避免东八区将近期 job 误判为陈旧。
- 既有严格 response allowlist、legacy link fail closed、generation CAS 和平台终态本地恢复路径保持通过。

### TDD 证据

generation 隔离 RED：

```bash
PYTHONPATH=backend pytest backend/tests/test_content_flow.py::test_existing_scheduled_tasks_reject_invalid_publish_action_after_migration backend/tests/test_api.py::test_once_task_patch_starts_new_generation_without_reusing_old_publish_job backend/tests/test_api.py::test_stale_generation_claim_does_not_backfill_old_publish_job -q
```

结果：退出码 `1`，`3 failed, 1 warning in 0.58s`。分别复现 run 无 generation、once PATCH 后复用旧 job、陈旧 claim 在 CAS 失败前回填旧 job。

中断恢复 RED：

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py::test_interrupted_pending_publish_job_executes_same_job backend/tests/test_api.py::test_stale_checking_login_job_moves_to_manual_interruption_failure backend/tests/test_api.py::test_recent_checking_login_job_is_not_changed_by_recovery_scan -q
```

结果：退出码 `1`，`3 failed, 1 passed, 1 warning in 0.62s`。pending job 在手动/扫描恢复后仍停留执行中，旧 checking_login 也无法退出执行中；近期 checking_login 对照保持不变。

UTC 边界补充 RED：近期 job 使用 SQLite UTC 时间后被错误标记失败，结果为 `1 failed, 1 passed, 1 warning in 0.48s`；转换超时边界为 UTC 后两项通过。

### GREEN 与最终验证

```bash
PYTHONPATH=backend pytest backend/tests/test_content_flow.py::test_existing_scheduled_tasks_reject_invalid_publish_action_after_migration backend/tests/test_api.py::test_once_task_patch_starts_new_generation_without_reusing_old_publish_job backend/tests/test_api.py::test_stale_generation_claim_does_not_backfill_old_publish_job backend/tests/test_api.py::test_repeat_task_starts_next_generation_with_a_new_publish_job backend/tests/test_api.py::test_interrupted_pending_publish_job_executes_same_job backend/tests/test_api.py::test_stale_checking_login_job_moves_to_manual_interruption_failure backend/tests/test_api.py::test_recent_checking_login_job_is_not_changed_by_recovery_scan -q
```

结果：退出码 `0`，`8 passed, 1 warning in 0.90s`。once 两次执行产生不同 job，job 总数与 publisher 调用均为 `2`，第二项目标题和 task 主题来自新代次；pending 手动/扫描恢复均为同一 job 且仅调用 publisher 一次；陈旧 checking_login 的 job/publisher 数不增加，近期项不变。

```bash
PYTHONPATH=backend pytest backend/tests/test_api.py backend/tests/test_content_flow.py -q
```

结果：退出码 `0`，`102 passed, 1 warning in 7.32s`。

```bash
PYTHONPATH=backend pytest backend/tests -q
```

结果：退出码 `0`，`169 passed, 1 warning in 6.82s`。

```bash
python3 -m compileall -q backend/app backend/tests
```

结果：无输出，退出码 `0`。

```bash
git diff --check
```

结果：无输出，退出码 `0`。唯一 warning 仍为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示。

### Concern

- 不确定发布状态超时后不会自动判断平台真实结果；这是刻意的 fail-closed 路径，必须由操作人员先核对微信公众号后台，再决定是否通过显式 retry 重试原 job。
- 平台终态与 SQLite 仍无法组成分布式事务；现有持久 job、同 job 恢复、generation CAS 与终态本地重放继续承担该边界。所有测试均使用 fake publisher，未执行真实平台写入。

## 最新三个 Important findings 修复：迁移归属、准备中断与 attempt ownership

### 修复内容

- legacy `generation=0` 的最新 run 只有在仍可能属于 task 当前代时才会对齐。task 已被 PATCH 为新一代“待执行”且 `publish_job_id` 已清空时，迁移不升级旧 run，也不沿旧 run 回填 task job；下一次执行创建新 project/job 并只发布新主题。
- 新增 pending orphan job 恢复。当前代 task/run 均为“执行中”但尚未 link 时，扫描按该代已持久化的 project、account、action 只接回唯一且未被其他 task/run 使用的 pending job；已有 package 原位复用，缺 package 时与 task/run link 在同一事务补建。多 job 或多 package 候选 fail closed；重启执行仍使用原 job。
- 每次 publish/resume claim 生成私有 attempt token，并把 claim 时捕获的 token 传入成功与失败持久化。回调 UPDATE 同时 CAS 当前 in-progress 状态和 token；stale timeout 或新 retry 改变所有权后，旧 attempt 的晚到成功/失败均不能覆盖新状态、平台 ID、evidence 或失败字段。
- 15 分钟 stale CAS 保持 UTC 边界与 fail-closed 语义；近期 active attempt 不会被 timeout，完成后仍可正常落库。

### TDD 证据

迁移归属 RED：

```bash
PYTHONPATH=backend pytest -q backend/tests/test_content_flow.py::test_migration_does_not_revive_completed_once_run_after_new_generation_patch
```

结果：退出码 `1`，`1 failed in 0.20s`。迁移把旧 completed run 从 `generation=0` 错误升级为新 task 的 `generation=2`。

prepare/link 中断 RED：

```bash
PYTHONPATH=backend pytest -q backend/tests/test_api.py::test_restart_adopts_pending_job_when_process_exits_before_task_link
```

结果：退出码 `1`，`1 failed, 1 warning in 0.39s`。重启扫描后 task 仍为“执行中”，不可达 pending job 未被执行。

attempt ownership RED：

```bash
PYTHONPATH=backend pytest -q backend/tests/test_content_flow.py::test_late_stale_attempt_cannot_overwrite_new_attempt_evidence backend/tests/test_content_flow.py::test_recent_active_attempt_is_not_timed_out
```

结果：退出码 `1`，`2 failed, 1 passed in 0.30s`。旧成功把新平台 ID 改回旧值，旧失败把新成功改为 failed；近期 active 对照保持 in-progress。

### GREEN 与最终验证

```bash
PYTHONPATH=backend pytest -q backend/tests/test_content_flow.py::test_migration_does_not_revive_completed_once_run_after_new_generation_patch backend/tests/test_content_flow.py::test_existing_task_backfills_publish_job_from_latest_run_or_package backend/tests/test_content_flow.py::test_legacy_task_links_backfill_through_package_and_retry_reuses_job backend/tests/test_content_flow.py::test_retry_publish_job_claim_allows_only_one_publisher_call backend/tests/test_content_flow.py::test_stale_retry_cannot_claim_a_later_failed_generation backend/tests/test_content_flow.py::test_late_stale_attempt_cannot_overwrite_new_attempt_evidence backend/tests/test_content_flow.py::test_recent_active_attempt_is_not_timed_out backend/tests/test_api.py::test_interrupted_pending_publish_job_executes_same_job backend/tests/test_api.py::test_restart_adopts_pending_job_when_process_exits_before_task_link backend/tests/test_api.py::test_stale_checking_login_job_moves_to_manual_interruption_failure backend/tests/test_api.py::test_recent_checking_login_job_is_not_changed_by_recovery_scan
```

结果：退出码 `0`，`14 passed, 1 warning in 1.14s`。包含参数化 late success/failure、run/package 两种 legacy 回填和 pending 手动/扫描两种既有恢复路径。

```bash
PYTHONPATH=backend pytest -q backend/tests/test_api.py backend/tests/test_content_flow.py
```

结果：退出码 `0`，`107 passed, 1 warning in 5.93s`。

```bash
PYTHONPATH=backend pytest -q backend/tests
```

结果：退出码 `0`，`174 passed, 1 warning in 7.65s`。

```bash
python3 -m compileall -q backend/app backend/tests
```

结果：无输出，退出码 `0`。

```bash
git diff --check
```

结果：无输出，退出码 `0`。唯一 warning 仍为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示；所有发布测试使用 fake publisher，未执行真实平台写入。

### Concern

- orphan adoption 只处理当前代执行中 task/run 对应的唯一未关联 pending job；候选不唯一时刻意不猜测，任务保持可人工检查状态。
- stale timeout 仍无法确认微信公众号后台是否已完成旧 attempt；系统只阻止旧回调覆盖新 attempt，是否发起显式 retry 仍需操作人员先核对平台。

## 最新独立审查修复：人工确认恢复中断与 prepare/package 窗口

### 修复内容

- 调度恢复扫描除既有“执行中”任务外，只额外选择 task 为“待人工确认”且当前 linked job 处于 `checking_login`、`uploading_assets`、`filling_content` 或 `publishing` 的精确组合。普通 `awaiting_confirmation` job 扫描后仍保持等待且 `resume_calls=0`，不会被自动 resume 或重复发布。
- 恢复函数要求 task/run 状态严格成对：既有“执行中/执行中”，或新增“待人工确认/待人工确认 + uncertain linked job”。stale job 超过 15 分钟后仍由协调器按 attempt token CAS 为 `failed`，task/run/package 最终化使用原“待人工确认”状态作为 CAS 条件；近期 job 只执行 stale 检查并保持不变。
- 新增 prepare job 已提交、package 尚未写入时抛出 `BaseException` 的真实故障注入。重启扫描补建唯一 package、接回同一 pending job，最终 `publisher_calls=1`、`job_count=1`、`package_count=1`。
- 新增 resume 专用 attempt ownership 回归：旧 resume 成功或失败回调在 stale timeout 和 retry 后晚到，均不能覆盖新 attempt 的平台 ID、公开 evidence、失败字段或私有 `response_json`。

### TDD 证据

RED：

```bash
PYTHONPATH=backend pytest -q backend/tests/test_api.py::test_restart_rebuilds_package_after_exit_between_prepare_and_package backend/tests/test_api.py::test_scheduler_fails_stale_claimed_resume_and_retry_reuses_job backend/tests/test_api.py::test_scheduler_recognizes_recent_claimed_resume_without_timing_it_out backend/tests/test_content_flow.py::test_late_stale_resume_cannot_overwrite_retry_evidence
```

结果：退出码 `1`，`2 failed, 3 passed, 1 warning in 0.75s`。stale 的等待确认任务仍停留在“待人工确认”，近期 resume attempt 也未进入 stale 检查；prepare/package 中断恢复和 late resume attempt ownership 在旧实现已通过，作为缺失的故障注入与行为锁定。

### GREEN 与最终验证

聚焦发布恢复与 attempt ownership：

```bash
PYTHONPATH=backend pytest -q backend/tests/test_api.py::test_repeat_task_waits_for_confirmation_before_scheduling_next_run backend/tests/test_api.py::test_interrupted_pending_publish_job_executes_same_job backend/tests/test_api.py::test_restart_adopts_pending_job_when_process_exits_before_task_link backend/tests/test_api.py::test_restart_rebuilds_package_after_exit_between_prepare_and_package backend/tests/test_api.py::test_stale_checking_login_job_moves_to_manual_interruption_failure backend/tests/test_api.py::test_recent_checking_login_job_is_not_changed_by_recovery_scan backend/tests/test_api.py::test_scheduler_fails_stale_claimed_resume_and_retry_reuses_job backend/tests/test_api.py::test_scheduler_recognizes_recent_claimed_resume_without_timing_it_out backend/tests/test_content_flow.py::test_resume_publish_job_claim_allows_only_one_publisher_call backend/tests/test_content_flow.py::test_late_stale_attempt_cannot_overwrite_new_attempt_evidence backend/tests/test_content_flow.py::test_late_stale_resume_cannot_overwrite_retry_evidence backend/tests/test_content_flow.py::test_recent_active_attempt_is_not_timed_out
```

结果：退出码 `0`，`15 passed, 1 warning in 1.24s`。

Task4：

```bash
cd backend && python -m pytest -q tests/test_api.py tests/test_content_flow.py::test_existing_scheduled_tasks_reject_invalid_publish_action_after_migration tests/test_content_flow.py::test_concurrent_task_execution_claims_generation_once tests/test_content_flow.py::test_concurrent_failed_task_retries_claim_once -k task
```

结果：退出码 `0`，`42 passed, 38 deselected, 1 warning in 3.08s`。

完整 backend：

```bash
PYTHONPATH=backend pytest -q backend/tests
```

结果：退出码 `0`，`179 passed, 1 warning in 8.34s`。

```bash
python3 -m compileall -q backend/app backend/tests
```

结果：无输出，退出码 `0`。

```bash
git diff --check
```

结果：无输出，退出码 `0`。唯一 warning 仍为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示；所有发布测试使用 fake publisher，未执行真实平台写入。

### Concern

- uncertain resume attempt 超时后仍无法自动确认微信公众号后台的真实结果，继续采用 fail-closed 并要求人工核对后显式 retry。
- prepare/package 恢复只接回当前代唯一、未被 task/run 占用的 pending job；候选不唯一时保持原有 fail closed，不猜测归属。

## 最新审查唯一 Important：terminal job 本地收尾恢复

### 修复内容

- 新增精确的待确认任务恢复状态集合：保留 `checking_login`、`uploading_assets`、`filling_content`、`publishing` 的 recent/stale 检查，并加入 `published`、`platform_draft`、`failed` 三种 terminal job；普通 `awaiting_confirmation` 不进入扫描。
- “待人工确认/待人工确认 + linked terminal job”恢复只调用既有本地最终化事务，不再次调用 publisher。task/run/package 的结果、平台 evidence、失败字段和 `published_at` 同步沿用 generation CAS，重复扫描不产生额外写入或新 job/package。
- once 成功保持最终结果且 `next_run_at=''`；repeat 成功回到“待执行”并计算下一执行时间；failed 对 once/repeat 均保持“失败”且不生成 `next_run_at`。
- 新增 resume 成功提交 terminal job 后、task/run/package 收尾前 `BaseException` 退出，以及 stale timeout 提交 failed 后、收尾前 `BaseException` 退出的真实重启回归。另覆盖 persisted `platform_draft` 重启恢复，三类 terminal 状态均验证 publisher 调用不增加。

### TDD 证据

RED：

```bash
PYTHONPATH=backend pytest -q backend/tests/test_api.py::test_scheduler_finalizes_resume_success_after_exit_before_local_sync backend/tests/test_api.py::test_scheduler_locally_finalizes_linked_platform_draft backend/tests/test_api.py::test_scheduler_finalizes_stale_failure_after_exit_before_local_sync
```

结果：退出码 `1`，`6 failed, 1 warning in 1.17s`。六个 once/repeat 场景均复现 task/run/package 停留“待人工确认”，linked job 已分别成为 `published`、`platform_draft`、`failed`，后续扫描无法收尾。

### GREEN 与最终验证

focused terminal/recent/stale/awaiting 对照：

```bash
PYTHONPATH=backend pytest -q backend/tests/test_api.py::test_task_scheduler_finalizes_resume_success_after_exit_before_local_sync backend/tests/test_api.py::test_task_scheduler_locally_finalizes_linked_platform_draft_after_restart backend/tests/test_api.py::test_task_scheduler_finalizes_stale_failure_after_exit_before_local_sync backend/tests/test_api.py::test_repeat_task_waits_for_confirmation_before_scheduling_next_run backend/tests/test_api.py::test_scheduler_fails_stale_claimed_resume_and_retry_reuses_job backend/tests/test_api.py::test_scheduler_recognizes_recent_claimed_resume_without_timing_it_out backend/tests/test_api.py::test_recent_checking_login_job_is_not_changed_by_recovery_scan
```

结果：退出码 `0`，`10 passed, 1 warning in 1.43s`。resume/stale 故障窗口重启后均只做本地收尾；once/repeat、三种 terminal、普通 awaiting、recent/stale、二次扫描幂等和 publisher 零新增调用全部通过。

Task4：

```bash
cd backend && python -m pytest -q tests/test_api.py tests/test_content_flow.py::test_existing_scheduled_tasks_reject_invalid_publish_action_after_migration tests/test_content_flow.py::test_concurrent_task_execution_claims_generation_once tests/test_content_flow.py::test_concurrent_failed_task_retries_claim_once -k task
```

结果：退出码 `0`，`48 passed, 38 deselected, 1 warning in 3.62s`。

完整 backend：

```bash
PYTHONPATH=backend pytest -q backend/tests
```

结果：退出码 `0`，`185 passed, 1 warning in 8.38s`。

```bash
python3 -m compileall -q backend/app backend/tests
```

结果：无输出，退出码 `0`。唯一 warning 仍为既有 FastAPI TestClient 的 Starlette/httpx 弃用提示；所有发布测试使用 fake publisher，未执行真实平台写入。

```bash
git diff --check
```

结果：无输出，退出码 `0`。

### Concern

- terminal job 只能重放已持久化的平台结论，不能弥合平台与 SQLite 之间的分布式事务；uncertain stale 状态仍按既有策略 fail closed，要求人工核对平台后再决定是否显式 retry。
