# 算疏智合 API 使用说明

启动服务：

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

常用接口：

```text
GET  /health
GET  /local-models/detect
POST /upload
POST /preview
POST /generate
POST /generate-sync
GET  /status/{task_id}
GET  /result/{company_slug}
GET  /download/{company_slug}/{filename}
```

本地 API 默认面向本机或内网使用。正式使用时请自行控制访问权限，不建议直接暴露到公网。
