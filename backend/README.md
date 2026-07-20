# 内容创作后端

## 启动方式

```bash
python3 -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
python3 -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

## 模型配置

在 `backend/.env` 中填写模型接口。文字生成使用 DeepSeek，图片生成使用火山 Ark / Doubao Seedream：

```bash
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=replace-with-your-deepseek-key
LLM_MODEL=deepseek-v4-pro

IMAGE_PROVIDER=doubao
IMAGE_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
IMAGE_API_KEY=replace-with-your-ark-key
IMAGE_MODEL=doubao-seedream-5-0-260128
```

`backend/.env` 已被 `.gitignore` 忽略，只用于本机运行，不要把密钥写入代码或提交到仓库。

## 微信公众号发布

发布凭据来自“账号矩阵”中的真实扫码登录，不需要用户提供 AppID、AppSecret、用户名或密码。每个账号使用独立 Chrome Profile 保存登录态，内容创作、草稿箱和任务中心复用同一账号会话。

V1 仅开放微信公众号真实发布，并提供两个独立动作：

- `同步到平台草稿箱`：文章真实进入微信公众号后台草稿箱。
- `一键发布`：文章真实提交发布；微信要求运营者扫码时，任务进入“待人工确认”，扫码后继续。

只有微信页面返回与本次操作绑定的平台回执，系统才会标记“已生成平台草稿”或“已发布”。登录失效、素材上传失败或缺少平台回执都会记录为失败，不会以本地发布包冒充成功。

React 前端默认请求 `http://127.0.0.1:8000`。

## 当前能力

- AI 选题推荐
- 公众号文章生成
- 封面图和正文配图生成
- 草稿自动保存与恢复
- 排版保存
- 微信公众号真实平台草稿与直接发布
