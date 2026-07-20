# WeChat Real Draft Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the false local “platform draft” success with a real WeChat Official Account draft API call and make scheduled tasks succeed only after WeChat returns a draft `media_id`.

**Architecture:** Add a focused WeChat API adapter that obtains a stable access token, uploads the selected cover and inline images, composes article HTML, and creates a draft. `ContentService` owns local package state and calls the adapter; all three product entry points keep using the same publish-package endpoint. Failed platform calls persist a failed package and task failure instead of creating a fake work record.

**Tech Stack:** Python 3.13, FastAPI, SQLite, httpx, pytest, React/TypeScript frontend.

## Global Constraints

- Credentials are read only from `backend/.env`; never return or log AppSecret or access tokens.
- Only a non-empty WeChat `media_id` proves platform draft success.
- Existing generated project 23 and scheduled task 17 must remain recoverable and retryable.
- The implementation creates a WeChat platform draft; it does not publicly publish the article.

---

### Task 1: WeChat API Adapter

**Files:**
- Create: `backend/app/publishing/__init__.py`
- Create: `backend/app/publishing/wechat.py`
- Create: `backend/tests/test_wechat_publishing.py`

**Interfaces:**
- Produces: `WeChatDraftPublisher.publish(payload: dict[str, Any], account_id: int | None) -> PublishResult`
- Produces: `PublishResult(status, platform_draft_id, platform_draft_url, response)`

- [ ] **Step 1: Write failing tests for stable token, cover upload, inline image upload, draft payload, missing credentials, and missing media_id.**
- [ ] **Step 2: Run `PYTHONPATH=backend pytest backend/tests/test_wechat_publishing.py -q` and verify the tests fail because the adapter does not exist.**
- [ ] **Step 3: Implement the minimal adapter using `httpx.Client`, environment credentials, token caching, multipart uploads, HTML escaping, and WeChat error parsing.**
- [ ] **Step 4: Run the focused tests and verify they pass.**

### Task 2: Publish State and Database Migration

**Files:**
- Modify: `backend/app/database.py`
- Modify: `backend/app/services.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `WeChatDraftPublisher.publish(...)`
- Produces: `ContentService.publish_project_to_platform(...)`

- [ ] **Step 1: Write failing tests proving a local package starts as `publishing`, becomes `platform_draft` only with a real `media_id`, becomes `failed` on adapter errors, and does not create a work before platform success.**
- [ ] **Step 2: Run the focused tests and verify the old false-success behavior fails them.**
- [ ] **Step 3: Add publish result columns and implement the state transition in `ContentService`.**
- [ ] **Step 4: Change scheduled task execution to call the real publish method and record `提交平台草稿` as the failure stage.**
- [ ] **Step 5: Migrate existing task 17/package 14 from false success to failed/retryable because no platform draft ID exists.**
- [ ] **Step 6: Run backend tests and verify all pass.**

### Task 3: API and Frontend Result Handling

**Files:**
- Modify: `backend/app/main.py`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/Creator.tsx`
- Modify: `frontend/src/pages/Tasks.tsx`

**Interfaces:**
- Consumes: publish package fields `status`, `platform_draft_id`, `platform_draft_url`, `failure_stage`, `failure_reason`.

- [ ] **Step 1: Add an API test that expects the endpoint to return the real platform result and an HTTP error for failed publishing.**
- [ ] **Step 2: Run the test and verify the old endpoint fails it.**
- [ ] **Step 3: Return the persisted publish result from FastAPI and update frontend messages from “发布包已生成” to real platform success/failure.**
- [ ] **Step 4: Run `npm run typecheck` and `npm run build` in `frontend`.**

### Task 4: Verification and Operator Setup

**Files:**
- Modify: `backend/.env.example`
- Modify: `README.md` if it exists; otherwise create `backend/README.md`.

- [ ] **Step 1: Document `WECHAT_APP_ID` and `WECHAT_APP_SECRET`, IP whitelist requirements, and the retry command without including secret values.**
- [ ] **Step 2: Run `PYTHONPATH=backend pytest backend/tests -q` and `python3 -m compileall -q backend/app`.**
- [ ] **Step 3: After credentials are configured locally, restart the backend and retry task 17.**
- [ ] **Step 4: Verify the WeChat platform draft list contains project 23 and the database package has a non-empty platform draft ID.**
