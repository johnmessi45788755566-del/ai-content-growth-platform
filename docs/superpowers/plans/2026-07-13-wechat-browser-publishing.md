# WeChat Browser Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse the Chrome Profile created by account-matrix QR login to send one WeChat Official Account article either to the platform draft box or directly to publication from Creator, Drafts, and scheduled Tasks.

**Architecture:** `ContentService` builds one normalized WeChat article payload, while a new `PublishCoordinator` owns persistence and status transitions. `WeChatBrowserPublisher` launches the selected account's saved Chrome Profile, drives the real `mp.weixin.qq.com` editor through CDP, and returns only platform-observed results. The frontend creates and polls the same publish job regardless of entry point.

**Tech Stack:** Python 3.13, FastAPI, SQLite, Chrome DevTools Protocol over `websocket-client`, React 18, TypeScript, Ant Design.

## Global Constraints

- V1 real publishing supports only `微信公众号`; 小红书、抖音、视频号 publishing remains unavailable.
- One content project targets one platform account; no cross-platform bulk publishing.
- `draft` and `publish` are separate actions and separate buttons.
- `publish` is the default scheduled-task action.
- A local package is never a success result; success requires a real WeChat platform page confirmation.
- If WeChat requests operator QR confirmation, the job becomes `awaiting_confirmation` and can be resumed after scanning.
- Reuse `platform_accounts.profile_path`; do not request or store AppID, AppSecret, username, or password.
- Existing static prototype files remain untouched.
- Preserve current uncommitted account, analytics, creator, draft, and task work.

---

## File Map

- Create `backend/app/publishing/models.py`: publish action, state, request, and result value objects.
- Create `backend/app/publishing/browser_gateway.py`: Chrome Profile lifecycle and CDP primitives.
- Replace `backend/app/publishing/wechat.py`: real WeChat editor automation and platform verification.
- Create `backend/app/publishing/coordinator.py`: publish-job state transitions and persistence.
- Modify `backend/app/database.py`: `publish_jobs` table and scheduled-task `publish_action`.
- Modify `backend/app/services.py`: payload creation, coordinator calls, task publishing, and compatibility package data.
- Modify `backend/app/main.py`: publish-job APIs and task request fields.
- Modify `backend/tests/test_wechat_publishing.py`: publisher unit tests with a fake browser gateway.
- Modify `backend/tests/test_content_flow.py`: coordinator and compatibility-flow tests.
- Modify `backend/tests/test_api.py`: HTTP contracts and scheduled-task action tests.
- Modify `frontend/src/types.ts`: publish job/action/status fields.
- Modify `frontend/src/services/api.ts`: create/get/retry/resume publish-job calls.
- Modify `frontend/src/pages/Creator.tsx`: two actions, job progress, and real account filtering.
- Modify `frontend/src/pages/Drafts.tsx`: publish actions for saved projects.
- Modify `frontend/src/pages/Tasks.tsx`: publish-action selector and result display.
- Modify `frontend/src/utils/taskPayload.ts`: serialize and hydrate `publish_action`.
- Modify `/Users/haocaijituan/Documents/最新/新媒体矩阵运营工作台PRD.md`: product flow, states, platform scope, and manual confirmation branch.

### Task 1: Publish Domain And Database State

**Files:**
- Create: `backend/app/publishing/models.py`
- Modify: `backend/app/database.py`
- Test: `backend/tests/test_content_flow.py`

**Interfaces:**
- Produces: `PublishAction = Literal["draft", "publish"]`.
- Produces: `PublishResult(status, platform_draft_id, platform_publish_id, platform_url, response, requires_confirmation)`.
- Produces: `publish_jobs` rows containing `project_id`, `account_id`, `action`, `status`, platform identifiers, failure fields, and timestamps.

- [ ] **Step 1: Write the failing schema test**

```python
def test_publish_jobs_schema_and_default_task_action(service):
    project = service.create_project("浏览器发布测试", platform="微信公众号")
    account = create_authorized_browser_account(service)
    job_id = service.db.execute(
        "INSERT INTO publish_jobs (project_id, account_id, platform, action, status) VALUES (?, ?, ?, ?, ?)",
        (project["id"], account["id"], "微信公众号", "draft", "pending"),
    )
    assert service.db.fetch_one("SELECT action, status FROM publish_jobs WHERE id = ?", (job_id,)) == {
        "action": "draft",
        "status": "pending",
    }
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `PYTHONPATH=backend pytest backend/tests/test_content_flow.py::test_publish_jobs_schema_and_default_task_action -q`

Expected: FAIL with `no such table: publish_jobs`.

- [ ] **Step 3: Add value objects and migrations**

```python
PublishAction = Literal["draft", "publish"]

@dataclass(frozen=True)
class PublishResult:
    status: str
    platform_draft_id: str = ""
    platform_publish_id: str = ""
    platform_url: str = ""
    response: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
```

Create `publish_jobs` with valid persisted states and add `scheduled_tasks.publish_action TEXT NOT NULL DEFAULT 'publish'`.

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run: `PYTHONPATH=backend pytest backend/tests/test_content_flow.py::test_publish_jobs_schema_and_default_task_action -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit the domain change**

```bash
git add backend/app/publishing/models.py backend/app/database.py backend/tests/test_content_flow.py
git commit -m "feat: add persistent publishing jobs"
```

### Task 2: WeChat Browser Publisher

**Files:**
- Create: `backend/app/publishing/browser_gateway.py`
- Replace: `backend/app/publishing/wechat.py`
- Modify: `backend/app/publishing/__init__.py`
- Test: `backend/tests/test_wechat_publishing.py`

**Interfaces:**
- Consumes: authorized account row with a valid `profile_path`.
- Produces: `WeChatBrowserPublisher.publish(payload, account, action) -> PublishResult`.
- Produces: gateway operations `open_profile`, `navigate`, `evaluate`, `set_file_input`, `screenshot`, and `close`.

- [ ] **Step 1: Replace credential tests with browser-flow tests**

```python
def test_wechat_browser_publisher_creates_verified_platform_draft(tmp_path):
    gateway = FakeWeChatGateway(outcome={"status": "platform_draft", "draft_id": "draft-42"})
    publisher = WeChatBrowserPublisher(gateway=gateway)
    result = publisher.publish(article_payload(tmp_path), browser_account(tmp_path), action="draft")
    assert result.status == "platform_draft"
    assert result.platform_draft_id == "draft-42"
    assert gateway.actions[-1] == "verify_draft"

def test_wechat_browser_publisher_pauses_for_operator_confirmation(tmp_path):
    gateway = FakeWeChatGateway(outcome={"status": "awaiting_confirmation", "screenshot": "confirm.png"})
    result = WeChatBrowserPublisher(gateway=gateway).publish(
        article_payload(tmp_path), browser_account(tmp_path), action="publish"
    )
    assert result.status == "awaiting_confirmation"
    assert result.requires_confirmation is True
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=backend pytest backend/tests/test_wechat_publishing.py -q`

Expected: FAIL because `WeChatBrowserPublisher` and the gateway do not exist.

- [ ] **Step 3: Implement minimal browser flow**

Implementation order inside `publish`:

```python
session = gateway.open_profile(Path(account["profile_path"]), "https://mp.weixin.qq.com/")
gateway.require_authenticated(session)
gateway.open_article_editor(session)
gateway.fill_article(session, payload)
if action == "draft":
    gateway.click_save_draft(session)
    return gateway.verify_draft(session, payload["title"])
gateway.click_publish(session)
return gateway.verify_publish_or_confirmation(session, payload["title"])
```

The real gateway must use the saved Profile, upload local images through CDP, preserve the visible browser for QR confirmation, and raise `WeChatPublishingError` when the account is logged out or WeChat never returns a platform result.

- [ ] **Step 4: Run publisher tests and confirm GREEN**

Run: `PYTHONPATH=backend pytest backend/tests/test_wechat_publishing.py -q`

Expected: all publisher tests pass without opening Chrome.

- [ ] **Step 5: Commit the adapter**

```bash
git add backend/app/publishing backend/tests/test_wechat_publishing.py
git commit -m "feat: publish WeChat articles through saved browser profiles"
```

### Task 3: Publish Coordinator And API

**Files:**
- Create: `backend/app/publishing/coordinator.py`
- Modify: `backend/app/services.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_content_flow.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**
- Produces: `ContentService.create_publish_job(project_id, account_id, action)`.
- Produces: `ContentService.get_publish_job(job_id)`, `retry_publish_job(job_id)`, `resume_publish_job(job_id)`.
- Produces HTTP: `POST /api/publish-jobs`, `GET /api/publish-jobs/{id}`, `POST /api/publish-jobs/{id}/retry`, `POST /api/publish-jobs/{id}/resume`.
- Preserves HTTP: `POST /api/content-projects/{id}/publish-package`, implemented through a `draft` publish job.

- [ ] **Step 1: Write failing coordinator/API tests**

```python
def test_create_publish_job_rejects_non_wechat_platform(client, authorized_xhs_account, project):
    response = client.post("/api/publish-jobs", json={
        "project_id": project["id"], "account_id": authorized_xhs_account["id"], "action": "publish"
    })
    assert response.status_code == 400
    assert "暂未开放" in response.json()["detail"]

def test_publish_job_requires_platform_observed_success(client, publisher_without_receipt, wechat_account, project):
    response = client.post("/api/publish-jobs", json={
        "project_id": project["id"], "account_id": wechat_account["id"], "action": "draft"
    })
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `PYTHONPATH=backend pytest backend/tests/test_api.py -k 'publish_job' -q`

Expected: FAIL with `404 Not Found`.

- [ ] **Step 3: Implement coordinator and endpoints**

Persist transitions before and after each external operation. Return `failed` as a normal job resource with `failure_stage` and `failure_reason`; reserve HTTP 4xx for invalid input/account/platform. Keep the compatibility endpoint response fields synchronized from the new job.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `PYTHONPATH=backend pytest backend/tests/test_api.py -k 'publish_job or publish_package' -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the coordinator/API**

```bash
git add backend/app/publishing/coordinator.py backend/app/services.py backend/app/main.py backend/tests/test_content_flow.py backend/tests/test_api.py
git commit -m "feat: unify real platform publishing jobs"
```

### Task 4: Scheduled Tasks Use The Same Publisher

**Files:**
- Modify: `backend/app/services.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `scheduled_tasks.publish_action` (`publish` default, `draft` optional).
- Produces: task success statuses `已发布`, `已生成平台草稿`, or `待人工确认` based on its publish job.

- [ ] **Step 1: Write failing scheduled-task tests**

```python
def test_scheduled_task_defaults_to_direct_publish(client, authorized_wechat_account):
    task = create_due_task(client, authorized_wechat_account)
    assert task["publish_action"] == "publish"

def test_scheduled_task_does_not_report_success_without_platform_receipt(client, publisher_without_receipt, account):
    task = execute_due_task(client, account, publish_action="publish")
    assert task["status"] == "失败"
    assert "平台回执" in task["failure_reason"]
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=backend pytest backend/tests/test_api.py -k 'scheduled_task and publish' -q`

Expected: FAIL because `publish_action` is absent or tasks always create drafts.

- [ ] **Step 3: Route task execution through publish jobs**

After layout, call `create_publish_job(..., action=task["publish_action"])`. Map job states exactly; repeated tasks calculate their next run only after a final successful platform result, while `awaiting_confirmation` remains paused.

- [ ] **Step 4: Run tests and confirm GREEN**

Run: `PYTHONPATH=backend pytest backend/tests/test_api.py -k 'task' -q`

Expected: task tests pass.

- [ ] **Step 5: Commit task integration**

```bash
git add backend/app/database.py backend/app/services.py backend/app/main.py backend/tests/test_api.py
git commit -m "feat: publish scheduled tasks through account sessions"
```

### Task 5: Frontend Publishing Entry Points

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/pages/Creator.tsx`
- Modify: `frontend/src/pages/Drafts.tsx`
- Modify: `frontend/src/pages/Tasks.tsx`
- Modify: `frontend/src/utils/taskPayload.ts`

**Interfaces:**
- Consumes API publish-job fields and methods from Task 3.
- Produces two explicit user commands: `同步到平台草稿箱` and `一键发布`.

- [ ] **Step 1: Add TypeScript contract checks**

Define:

```ts
export type PublishAction = 'draft' | 'publish';
export type PublishJobStatus =
  | 'pending' | 'checking_login' | 'uploading_assets' | 'filling_content'
  | 'platform_draft' | 'publishing' | 'awaiting_confirmation' | 'published' | 'failed';
```

Run: `npm --prefix frontend run typecheck`

Expected before implementation: FAIL on missing API methods/fields used by pages.

- [ ] **Step 2: Add API client methods**

```ts
createPublishJob(payload: { project_id: number; account_id: number; action: PublishAction })
getPublishJob(id: number)
retryPublishJob(id: number)
resumePublishJob(id: number)
```

- [ ] **Step 3: Update Creator and Drafts**

Both pages load only authorized WeChat accounts for V1. The modal displays one account selector and two separate buttons. Poll until `platform_draft`, `published`, `awaiting_confirmation`, or `failed`; show platform IDs/URL on success and the confirmation instruction when paused.

- [ ] **Step 4: Update Tasks**

Add required `发布方式` with options `一键发布` and `同步到平台草稿箱`, defaulting to `一键发布`. Display publish action and real publish result in list/detail views.

- [ ] **Step 5: Run frontend verification**

Run: `npm --prefix frontend run typecheck`

Run: `npm --prefix frontend run build`

Expected: both exit with code 0.

- [ ] **Step 6: Commit frontend integration**

```bash
git add frontend/src/types.ts frontend/src/services/api.ts frontend/src/pages/Creator.tsx frontend/src/pages/Drafts.tsx frontend/src/pages/Tasks.tsx frontend/src/utils/taskPayload.ts
git commit -m "feat: add draft and direct publishing actions"
```

### Task 6: PRD And Operating Documentation

**Files:**
- Modify: `/Users/haocaijituan/Documents/最新/新媒体矩阵运营工作台PRD.md`
- Modify: `backend/README.md`
- Modify: `backend/.env.example`

**Interfaces:**
- Documents browser Profile dependency, V1 platform boundary, dual actions, status meanings, and QR confirmation.

- [ ] **Step 1: Remove obsolete official-API credential instructions**

Delete AppID/AppSecret as end-user prerequisites and document that the account matrix login session is the publishing credential.

- [ ] **Step 2: Update product flow**

Document: select one authorized WeChat account, select one action, create job, observe platform response, pause for optional operator confirmation, and only then mark success.

- [ ] **Step 3: Update scheduled-task flow**

Document direct publish as default, platform draft as optional, and keep non-WeChat publishing unavailable in V1.

- [ ] **Step 4: Verify terminology**

Run: `rg -n "AppID|AppSecret|优先尝试直发|多账号|跨平台" '/Users/haocaijituan/Documents/最新/新媒体矩阵运营工作台PRD.md' backend/README.md backend/.env.example`

Expected: no obsolete end-user publishing requirement remains; any retained occurrence is explicitly marked as unsupported legacy design.

- [ ] **Step 5: Commit workspace documentation**

```bash
git add backend/README.md backend/.env.example
git commit -m "docs: document browser-session publishing"
```

### Task 7: Full Verification And Controlled Live Test

**Files:**
- Verify all files changed above.

- [ ] **Step 1: Run backend tests**

Run: `PYTHONPATH=backend pytest backend/tests -q`

Expected: all tests pass.

- [ ] **Step 2: Compile backend**

Run: `python3 -m compileall -q backend/app`

Expected: exit code 0.

- [ ] **Step 3: Run frontend checks**

Run: `npm --prefix frontend run typecheck`

Run: `npm --prefix frontend run build`

Expected: both exit code 0.

- [ ] **Step 4: Start local services and inspect all three entry points**

Backend:

```bash
cd "/Users/haocaijituan/Documents/New project 2"
python3 -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd "/Users/haocaijituan/Documents/New project 2/frontend"
npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173/creator`, `http://127.0.0.1:5173/drafts`, and `http://127.0.0.1:5173/tasks`.

- [ ] **Step 5: Perform a controlled platform draft test**

After obtaining explicit confirmation for the external write, choose the authorized `理想说AI` account, sync one uniquely titled article to the platform draft box, and verify the same title is visible in the real WeChat backend.

- [ ] **Step 6: Perform a controlled direct-publish test**

After a second explicit confirmation for public publication, publish one uniquely titled article, complete any WeChat operator QR confirmation, and verify the article in the real publication/history page. Record the platform ID/URL in `publish_jobs`.

- [ ] **Step 7: Review diff and commit final fixes**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intentional existing and implementation changes remain.
