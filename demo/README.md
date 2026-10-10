# 记忆体验 Demo

线下活动用的 PowerContext 记忆能力体验页：口令进门 → 和「小忆」聊天 →
开关记忆对比效果 → 面板上遗忘/修改记忆。

设计文档：`docs/superpowers/specs/2026-10-10-memory-demo-design.md`（如存在）

## 运行

```bash
uv sync --extra server --extra builtin
cp demo/.env.example demo/.env   # 填口令与模型 key
uv run python -m demo            # 0.0.0.0:8080，数据落 demo/data/
```

打开 `http://<服务器>:8080`。SQLite 落盘，重启服务记忆不丢；
活动结束后 `rm -rf demo/data` 清场，改 `DEMO_PASSPHRASE` 换一批体验者。

## 体验者剧本（现场引导词）

1. 输入口令和昵称进入；
2. 告诉小忆两三件关于自己的事（口味、爱好、工作）；
3. 等右侧「我的记忆」出现新条目（自动提取，通常几秒）；
4. 点「新会话」——聊天窗清空，再问「你还记得我吗」；
5. 关闭记忆开关再问同样的问题，对比小忆的反应；
6. 在面板上「遗忘」或「修改」一条记忆，再问一次看效果。

## 排障

- 页面打不开：确认 `DEMO_PORT` 未被占用，服务器防火墙/安全组放行该端口。
- 聊天报「模型调用失败」：检查 `DEMO_LLM_*` 三项与端点连通性（key、base_url、模型名）。
- 「我的记忆」一直为空：自动提取需要 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_*`
  配置正确——注意 `GENERATION_MODEL` 必须用 `openai-chat:` 前缀（`openai:` 前缀走
  Responses API，兼容端点会 404/503，启动日志会打警告）；`GENERATION_HEADERS` 是
  JSON 字符串。
- 服务器配了 HTTP_PROXY 且推理端点是本机 127.0.0.1 时提取会 502：NO_PROXY 里 CIDR
  写法（127.0.0.0/8）不被 httpx 识别，需加入精确条目 `127.0.0.1`。
- 回归测试：`uv run --no-sync pytest demo/tests -v`（无模型也能全绿）。

## 结构

`demo/app.py` FastAPI 路由与生命周期 · `demo/server.py` 进程内 PowerContext 服务 ·
`demo/agent.py` 聊天编排 · `demo/static/index.html` 单文件前端 · `demo/data/` 运行数据（gitignore）。
