# 真实平台草稿发布设计

## 目标

把内容创作、草稿箱和任务中心的发布动作统一接到真实平台。成功的定义不是“本地生成了发布包”，而是目标平台确认已经创建草稿，并返回可持久化的草稿标识，或浏览器自动化在平台页面验证草稿已保存。

本轮支持微信公众号、小红书、抖音、视频号。微信公众号使用官方服务端 API；小红书、抖音、视频号使用当前已保存登录态的独立 Chrome 配置目录执行浏览器自动化。

## 已确认的微信官方接口

1. 获取稳定版接口调用凭据：`POST https://api.weixin.qq.com/cgi-bin/stable_token`。请求体包含 `grant_type=client_credential`、`appid`、`secret`，普通模式不强制刷新；服务端缓存 token 和过期时间。
2. 上传封面永久素材：调用永久图片素材上传接口，取得草稿文章要求的 `thumb_media_id`。
3. 上传正文图片：调用“上传发表内容中的图片”接口，把本地正文配图转换成微信可访问 URL，再写入文章 HTML。
4. 新增草稿：`POST https://api.weixin.qq.com/cgi-bin/draft/add?access_token=...`。只有返回非空 `media_id` 才判定“已生成平台草稿”。

官方文档：

- [获取稳定版接口调用凭据](https://developers.weixin.qq.com/doc/service/api/base/api_getstableaccesstoken.html)
- [素材管理](https://developers.weixin.qq.com/doc/service/guide/product/asset.html)
- [上传发表内容中的图片](https://developers.weixin.qq.com/doc/service/api/notify/message/api_uploadimage.html)
- [新增草稿](https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add)

## 架构

新增统一发布服务 `PublishingService`，业务入口只向它提交标准化的发布请求，不直接判断平台：

- `WeChatDraftPublisher`：公众号官方 API 适配器。
- `XiaohongshuDraftPublisher`：小红书创作中心浏览器自动化适配器。
- `DouyinDraftPublisher`：抖音创作者中心浏览器自动化适配器。
- `ChannelsDraftPublisher`：视频号助手浏览器自动化适配器。

发布请求包含内容项目、目标账号、标题、排版后的正文 HTML、封面图、正文配图。发布结果统一包含：状态、平台草稿 ID、草稿 URL、平台原始返回、失败阶段和失败原因。

内容创作、草稿箱和任务中心复用同一服务：

1. 先生成或更新本地 `publish_package`，状态为 `publishing`。
2. 调用目标平台适配器。
3. 真实平台确认成功后更新为 `platform_draft`。
4. 失败则更新为 `failed`，保留本地发布包和错误信息，供重新发布或人工接管。

## 微信公众号流程

1. 读取所选账号对应的 AppID/AppSecret 配置；密钥只放在本机 `.env`，不进入前端、数据库日志或 Git。
2. 获取并缓存 stable access token。
3. 校验排版内容、已选封面和本地图片文件。
4. 上传封面得到 `thumb_media_id`。
5. 上传每张正文配图，替换 HTML 中图片地址。
6. 调用新增草稿接口。
7. 保存返回的 `media_id` 和平台响应。

缺少开发接口权限、AppID/AppSecret、IP 白名单或素材不合规时直接失败，向前端展示微信错误码和可操作原因，不降级为虚假成功。

## 小红书、抖音、视频号流程

1. 根据账号 `profile_path` 启动独立 Chrome，复用用户扫码后的真实登录态。
2. 打开对应平台的图文发布/创作页面。
3. 填写标题和正文，通过 CDP 文件上传能力上传封面和正文图片。
4. 点击平台的“保存草稿”动作；如果平台没有独立按钮，则使用平台确认保留草稿的退出流程。
5. 跳转或查询平台草稿列表，验证标题或草稿标识确实出现。
6. 保存草稿 ID/URL；只有验证成功才返回 `platform_draft`。

平台 DOM 和流程变化时返回 `failed`，记录当前 URL、失败步骤和脱敏页面信息，并提供“人工接管”入口打开同一 Chrome 配置目录继续操作。不得在未验证平台草稿存在时标记成功。

## 数据结构

`publish_packages` 增加：

- `platform_draft_id`
- `platform_draft_url`
- `platform_response_json`
- `failure_stage`
- `failure_reason`
- `published_at`
- `updated_at`

状态限定为：`pending_publish`、`publishing`、`platform_draft`、`failed`。

`platform_works` 只在真实平台草稿创建成功后写入或更新，状态为“平台草稿”；不再在创建本地发布包时提前生成作品记录。

## 前端行为

- 内容创作：点击发布后显示“正在提交至平台草稿箱”，成功展示平台草稿结果，失败展示具体阶段和重试入口。
- 草稿箱：完成排版的草稿可直接选择授权账号并发布；也可继续进入内容创作编辑。
- 任务中心：到点后走同一发布服务。重复任务单次失败保留失败记录，不能把本地包作为成功；批量重试重新调用真实发布服务。

## 安全与外部写入

- 微信密钥只从后端环境变量读取，错误日志不得打印密钥或完整 token。
- 浏览器登录态按账号独立保存，不复制到前端。
- 自动化不会点击“公开发布”，本轮只创建平台草稿。
- 真实联调会在用户指定账号中创建明确标注的测试草稿；执行该外部写入前需要用户确认测试账号和测试内容。

## 验收

1. 公众号配置有效凭据后，平台草稿箱能看到标题、正文、封面和正文配图，后端保存微信返回的 `media_id`。
2. 小红书、抖音、视频号分别使用已登录账号创建草稿，并能在各平台草稿列表中找到。
3. 三个业务入口对同一内容项目返回一致的发布结果和错误结构。
4. 断网、登录失效、微信权限不足、素材上传失败时均显示失败，不产生虚假平台作品。
5. 后端单元测试覆盖适配器路由、状态迁移、微信请求格式和任务失败路径；浏览器端使用平台测试账号做真实联调。

## 实施顺序

1. 统一发布结果、数据库迁移和状态机。
2. 微信公众号官方草稿适配器和测试。
3. 内容创作、草稿箱、任务中心接入统一服务。
4. 小红书、抖音、视频号浏览器适配器，逐个平台真实联调。
5. 全链路回归并更新 PRD 的真实能力边界。
