# 小红书 Patchright 发布技术方案

## 1. 目标

将小红书图文发布链路从当前 CDP 坐标点击方式，迁移为参考 `dreammis/social-auto-upload` 的 Patchright/Playwright locator 点击方式。

核心目标：

- 图片上传使用 `set_input_files`，不依赖 macOS 原生文件选择器。
- 一键发布使用 `button:has-text("发布")` 点击。
- 同步到草稿箱使用 `button:has-text("暂存离开")` 点击。
- 不再使用 `clickPoint`、`Input.dispatchMouseEvent`、宿主组件比例坐标点击。
- 微信公众号和小红书代码保持分离，后续互不影响。

## 2. 当前问题

当前本地小红书发布器虽然会识别 `xhs-publish-btn`，但实际最后一步仍然是：

```text
xhs-publish-btn -> 计算 clickPoint -> CDP dispatchMouseEvent
```

15:39 一键发布 trace 显示：

```text
xhs.publish.click_dispatched method=cdp_mouse x=690 y=684.5
```

15:44 同步草稿箱 trace 显示：

```text
xhs.draft.click_dispatched method=cdp_mouse x=486 y=684.5
```

这说明一键发布和同步草稿箱都还没有真正切到 GitHub 项目的 locator 点击方式。

## 3. GitHub 项目参考方式

`dreammis/social-auto-upload` 当前小红书图文上传使用 Patchright/Playwright 思路：

```python
await page.locator('input[type="file"]').set_input_files(images)
await page.locator('button:has-text("发布")').click()
```

参考项目：

- https://github.com/dreammis/social-auto-upload
- https://raw.githubusercontent.com/dreammis/social-auto-upload/main/uploader/xiaohongshu_uploader/main.py

GitHub 项目主要提供发布能力；同步草稿箱需要在同样 locator 思路下扩展为：

```python
await page.locator('button:has-text("暂存离开")').click()
```

## 4. 模块设计

新增小红书 Patchright 发布器，不改微信公众号发布器：

```text
backend/app/publishing/
  wechat.py                     # 微信公众号原有逻辑
  xiaohongshu.py                # 小红书旧 CDP 逻辑，临时保留
  xiaohongshu_patchright.py     # 新增，小红书 Patchright 实现
  router.py                     # 小红书路由切换到 PatchrightPublisher
```

建议新增类：

```python
class XiaoHongShuPatchrightPublisher:
    async def publish_with_progress(...)
    async def _prepare_note(...)
    async def _publish_note(...)
    async def _save_draft_note(...)
```

保留现有：

- `PublishResult`
- `PublishCoordinator`
- 发布作业日志
- 任务中心状态
- 监控汇总

只替换小红书平台内部浏览器自动化实现。

## 5. 账号登录态方案

第一阶段优先复用现有小红书 Chrome Profile，降低改造成本：

```python
context = await browser_type.launch_persistent_context(user_data_dir=profile_path)
```

第二阶段再收敛到 GitHub 项目更接近的 `storage_state` 方式：

```python
context = await browser.new_context(storage_state=storage_state_path)
```

账号矩阵后续可增加或复用字段：

```text
storage_state_path
```

用于保存小红书登录态文件。

## 6. 图文发布共同流程

一键发布和同步草稿箱共用准备阶段：

```python
async with async_playwright() as p:
    browser = await p.chromium.launch(...)
    context = await browser.new_context(storage_state=account_file)
    page = await context.new_page()

    await page.goto(
        "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=image"
    )
    await page.wait_for_url("**/publish/publish**")

    upload_input = page.locator('input[type="file"][accept*="image"]').first
    if not await upload_input.count():
        upload_input = page.locator("div[class^='upload-content'] input[class='upload-input']").first

    await upload_input.wait_for(state="attached", timeout=30000)
    await upload_input.set_input_files(image_paths)

    await page.locator('input[placeholder*="填写标题"]').first.wait_for(
        state="visible",
        timeout=90000,
    )

    await page.locator('input[placeholder*="填写标题"]').fill(title[:20])

    desc = page.locator('p[data-placeholder*="输入正文描述"]').first
    await desc.click()
    await page.keyboard.type(note)
```

## 7. 正文长度前置校验

小红书正文限制为 1000 字。发布前必须前置校验：

```python
if len(note) > 1000:
    raise XiaoHongShuPublishingError("小红书正文超过 1000 字，请缩短后再发布")
```

不建议默认截断，避免误发残缺内容。后续如需自动压缩，应放到内容生成阶段完成。

## 8. 一键发布线路

一键发布只点击“发布”：

```python
async def _publish_note(page):
    await page.wait_for_timeout(2000)

    button = page.locator('button:has-text("发布")').last
    await button.wait_for(state="visible", timeout=30000)
    await button.click()

    await page.wait_for_url("**/publish/success?**", timeout=60000)
```

成功条件：

- 页面进入 `/publish/success`
- 返回 `PublishResult(status="published")`
- `response.source = "xiaohongshu_platform"`
- `response.event = "note_published"`
- `response.corroboration = "publish_success_url"`

## 9. 同步到草稿箱线路

同步到草稿箱只点击“暂存离开”：

```python
async def _save_draft_note(page):
    await page.wait_for_timeout(2000)

    button = page.locator('button:has-text("暂存离开")').last
    await button.wait_for(state="visible", timeout=30000)
    await button.click()

    await page.wait_for_function(
        "() => !location.pathname.includes('/publish/publish')",
        timeout=60000,
    )
```

草稿成功判定建议至少满足一个：

- URL 离开 `/publish/publish`
- 页面进入首页、笔记管理或草稿相关页面
- 页面出现“已暂存”“保存成功”等提示

不能只以点击完成作为草稿成功。

## 10. 需要删除或禁用的旧逻辑

小红书最终按钮点击中应删除或禁用：

```text
clickPoint
fallback_ratio
_dispatch_note_action_click
_publish_click_rect
Input.dispatchMouseEvent
xhs-publish-btn host 坐标点击
```

`xhs-publish-btn` 可以保留为监控诊断字段，但不能再作为点击入口。

## 11. 日志与监控

新增 trace 事件：

```text
xhs.patchright.launch
xhs.patchright.goto_publish
xhs.upload.locator_ready
xhs.upload.set_input_files_done
xhs.content.length_check
xhs.publish.locator_found
xhs.publish.locator_clicked
xhs.publish.success_url
xhs.draft.locator_found
xhs.draft.locator_clicked
xhs.draft.saved_confirmed
```

失败快照必须包含：

- 当前 URL
- 标题长度
- 正文长度
- 是否超过 1000 字
- `button:has-text("发布")` 数量
- `button:has-text("暂存离开")` 数量
- 可见按钮文本列表
- 截图

## 12. 测试方案

后端单测建议覆盖：

1. 一键发布必须用 locator 点击“发布”。

```python
assert fake_page.clicked_text == "发布"
assert "cdp_mouse" not in trace
```

2. 草稿必须用 locator 点击“暂存离开”。

```python
assert fake_page.clicked_text == "暂存离开"
```

3. 正文超过 1000 字前置失败。

```python
with pytest.raises(XiaoHongShuPublishingError, match="超过 1000"):
    ...
```

4. 图片上传必须走 `set_input_files`。

```python
assert fake_input.files == image_paths
```

5. 两条线路共享准备阶段，但出口分离。

```text
publish -> _publish_note
draft   -> _save_draft_note
```

## 13. 实施顺序

1. 新增 `xiaohongshu_patchright.py`，先不删除旧 CDP 实现。
2. 增加 Patchright/Playwright 依赖和浏览器启动配置。
3. 小红书 router 切到新 publisher。
4. 保留旧 CDP publisher 作为临时回滚点，但默认不使用。
5. 跑一次一键发布，确认 trace 为 `xhs.publish.locator_clicked`。
6. 跑一次同步草稿箱，确认 trace 为 `xhs.draft.locator_clicked`。
7. 两条线路稳定后，删除小红书 CDP 坐标点击代码。

## 14. 验收标准

改完后 trace 中不应再出现：

```text
method="cdp_mouse"
clickPoint
Input.dispatchMouseEvent
```

改完后应出现：

```text
xhs.publish.locator_clicked button_text=发布
xhs.draft.locator_clicked button_text=暂存离开
```

一键发布验收：

- 图片上传成功。
- 标题和正文填写成功。
- 正文未超过 1000 字。
- 点击“发布”。
- 进入 `/publish/success`。
- 任务状态为“已发布”。

同步草稿箱验收：

- 图片上传成功。
- 标题和正文填写成功。
- 点击“暂存离开”。
- 页面离开发布编辑页或出现草稿保存成功信号。
- 任务状态为“已生成平台草稿”。

