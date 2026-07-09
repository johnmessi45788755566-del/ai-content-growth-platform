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

前端 `prototype.html` 默认请求 `http://127.0.0.1:8000`。

## 当前能力

- AI 选题推荐
- 公众号文章生成
- 封面图和正文配图生成
- 草稿自动保存与恢复
- 排版保存
- 待发布包生成
