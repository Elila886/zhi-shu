# 🤖 LangGraph RAG Agent

一个基于 **FastAPI**、**LangGraph** 和 **React** 构建的智能体式检索增强生成（RAG）系统，支持 NDJSON 流式响应与 **PostgreSQL + pgvector** 向量存储。系统提供相互独立的用户端和管理端、安全的刷新 Cookie 会话、带持久记忆的多轮对话，以及文档检索和网页搜索工具。

## 🚀 功能特性

* **基于 LangGraph 的智能体式 RAG**：使用 ReAct 风格的智能体，并配备文档检索和网页搜索工具
* **端到端流式响应**：React 界面实时处理 token、工具调用和工具结果事件
* **线程式对话**：每个用户拥有独立的对话线程，并通过 Postgres checkpointer 持久化历史记录
* **PostgreSQL + pgvector**：用于存储向量，并对用户上传的文档进行语义检索
* **安全会话与 RBAC**：访问令牌仅保存在内存，刷新令牌使用轮换的 HttpOnly Cookie，管理端会话独立
* **文档摄取**：支持 PDF、DOCX 和 TXT 文件，并提供文本切块与异步索引
* **工具能力**：内置 `retrieve_user_documents` 文档检索工具，以及 Tavily 网页搜索集成
* **异步优先的后端**：FastAPI + SQLAlchemy 2.0 async，具备生产级日志和健康检查

## 💻 技术栈

* **后端**：FastAPI、LangGraph、LangChain、SQLAlchemy、Pydantic v2
* **向量存储**：PostgreSQL + pgvector，通过 `langchain-postgres` 使用
* **Checkpointer**：LangGraph Postgres Checkpointer，异步版本
* **前端**：React、TypeScript、Vite、TanStack Query、React Router、Nginx
* **大模型 / Embedding**：兼容 OpenAI 接口的模型，支持配置 base URL

## 📋 前置要求

* Python 3.12+
* Docker 和 Docker Compose，推荐用于运行 Postgres 和完整技术栈

## 📦 快速开始：Docker Compose

1. 复制环境变量模板，并编辑对应配置：

```bash
cp env.example .env
```

2. 启动完整服务：

```bash
docker compose up --build
```

服务地址：

* 用户端：`http://localhost:8501`
* 管理端：`http://localhost:8502`
* 后端 API：`http://localhost:8000/api/v1`（也可用 `BACKEND_HOST_PORT` 覆盖）
* API 文档：`http://localhost:8000/api/v1/docs`

注意：

* `pgvector/pgvector:pg16` 镜像已经包含 `vector` 扩展。如果你使用自己的 Postgres，请确保已经启用：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

* Nginx 提供 React 静态资源并代理 `/api`，生产浏览器不需要直接访问后端宿主端口。
* 修复验收可同时启动独立 React 预览，不会替换当前 `8501/8502`：

```bash
docker compose -f docker-compose.yml -f docker-compose.preview.yml --profile preview up -d --build frontend-preview admin-preview
```

预览默认使用用户端 `8511` 和管理端 `8512`；先检查端口占用，不要停止无关进程。

Windows 可使用只读预检脚本：

```powershell
.\scripts\check-preview-ports.ps1
```

预览脚本会从未跟踪的根目录 `.env` 读取 `PREVIEW_*` 变量（同名的当前 PowerShell 环境变量优先）。其中用户端和管理端账号必须是已激活的本地账号；完整预览验证另需真实 AI 所属会话 ID 与模型名。不要把密码写入文档、源码或命令输出。

## 🧰 本地开发

### 1）后端：FastAPI

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate     # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

确保已经运行一个带有 pgvector 的 Postgres 实例。可以使用 Docker 示例：

```bash
docker run --name langgraph_postgres -p 5432:5432 \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=test -e POSTGRES_DB=langgraph_db \
  -d pgvector/pgvector:pg16
```

运行 API：

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --reload-dir ./app
```

### 2）前端：React

```bash
cd web
npm install
npm run dev:user   # http://localhost:5173
npm run dev:admin  # 另开终端，http://localhost:5174
```

常用检查命令为 `npm run typecheck`、`npm test` 和 `npm run build`。Playwright 按 `test:e2e:non-ai`、`test:e2e:admin`、`test:e2e:real-ai` 与 `test:e2e:mobile` 分组；两个应用通过 npm workspace 共享 API、类型和基础组件。

隔离后端集成测试使用临时 PGVector 数据卷，不连接业务数据库：

```bash
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from backend-test backend-test
```

会写数据的 E2E 必须使用 `docker-compose.e2e.yml`，它以独立 Compose 项目运行，并把 PostgreSQL 数据放在 tmpfs 中。先在当前终端设置六个 `E2E_USER_*`、`E2E_ADMIN_*`、`E2E_SUPER_ADMIN_*` 账号变量，再运行：

```bash
./scripts/run-isolated-acceptance.ps1
```

真实模型与 Embedding 完整路径还需显式设置 `E2E_REAL_AI=1`、真实 provider 凭据及 provider/model/embedding 配置；缺少任一项会以“环境阻塞”非零退出，不再以 skip 掩盖。统一执行器使用唯一 Compose 项目和 tmpfs，并只清理该项目的容器和网络。不要把凭据写入源码或命令输出。

任何人工审查的预览发布前，先运行 `scripts/start-preview-after-acceptance.ps1` 与 `scripts/verify-preview.ps1`。

人工确认独立预览后，正式接管前可用以下命令复核主入口 `8501/8502` 的用户/管理端权限隔离、HttpOnly 刷新 Cookie 与服务重启后的会话恢复。该命令只重启主 `backend`、`frontend` 和 `admin` 容器，不调用真实模型：

```powershell
.\scripts\verify-formal-takeover.ps1 -RestartServices
```


## 🔧 环境变量

在项目根目录创建 `.env` 文件，后端和前端都会读取它。关键配置如下：

核心大模型配置：

* `OPENAI_API_KEY`
* `MODEL_PROVIDER`，例如 `openai`
* `MODEL_NAMES`，JSON 列表，例如 `["gpt-4o", "gpt-4o-mini"]`
* `MODEL_BASE_URL`，可选，用于 OpenAI 兼容接口
* `EMBEDDINGS_MODEL_NAME`，例如 `text-embedding-3-large`
* `EMBEDDINGS_BASE_URL`，可选
* `TAVILY_API_KEY`，用于网页搜索工具

认证与 token：

* `TOKEN_BEARER_URL`，默认 `/api/v1/auth/login`
* `JWT_SECRET`，建议使用强随机值
* `JWT_ALGORITHM`，例如 `HS256`
* `ACCESS_TOKEN_EXPIRY_MINS`，例如 `1440`
* `REFRESH_TOKEN_EXPIRY_DAYS`，例如 `1`
* `FRONTEND_ORIGINS`，本地跨域开发使用的明确 JSON 白名单
* `COOKIE_SECURE`，生产 HTTPS 环境应设为 `true`
* `COOKIE_SAMESITE`，默认 `lax`
* `REFRESH_COOKIE_NAME` / `ADMIN_REFRESH_COOKIE_NAME`

数据库与向量存储：

* `POSTGRES_HOST`，例如本地运行时使用 `127.0.0.1`，Docker 中使用 `postgres`
* `POSTGRES_PORT`，例如 `5432`
* `POSTGRES_USER`，例如 `postgres`
* `POSTGRES_PASSWORD`，例如 `test`
* `POSTGRES_DATABASE`，例如 `langgraph_db`
* `PGVECTOR_COLLECTION_NAME`，例如 `my_collection`

端口：

* `FRONTEND_PORT` / `ADMIN_PORT`，React 默认使用 `8501/8502`

示例配置可以在 `env.example` 中找到。

## 🧩 API 概览

基础 URL：`/api/v1`

认证：

* `POST /auth/signup`
* `POST /auth/login`
* `POST /auth/refresh-token`
* `POST /auth/logout`
* `POST /auth/admin/login`
* `POST /auth/admin/refresh-token`
* `POST /auth/admin/logout`

公开运行时配置：

* `GET /config/public`

用户：

* `GET /users/me`
* `PUT /users/user-profile/{user_id}`
* `DELETE /users/user-profile/{user_id}`

管理端：

* `GET /admin/me`
* `GET /admin/overview`
* `GET /admin/health`
* `GET /admin/users`
* `PATCH /admin/users/{user_id}`
* `POST /admin/users/{user_id}/reset-password`
* `GET /admin/documents`
* `DELETE /admin/documents/{document_id}`
* `GET /admin/audit-logs`（仅超级管理员）

对话线程：

* `POST /threads/`：创建线程
* `GET /threads/`：列出线程
* `GET /threads/{thread_id}`：获取指定线程
* `PATCH /threads/{thread_id}`：更新标题
* `DELETE /threads/{thread_id}`：删除线程，并级联清理记忆和向量

文档：

* `GET /documents/{thread_id}`：列出文档
* `POST /documents/upload/{thread_id}`：上传文档并异步索引
* `DELETE /documents/{document_id}`：删除文档，并从 pgvector 中删除对应文本块

聊天与流式输出：

* `POST /chat/{thread_id}`：带认证的流式智能体聊天，支持工具和记忆
* `GET /chat/{thread_id}`：获取持久化的聊天历史

API 文档：

* Swagger UI：`http://localhost:8000/api/v1/docs`
* ReDoc：`http://localhost:8000/api/v1/redoc`

## 📡 流式协议

聊天流式接口以换行分隔的 JSON 事件形式进行输出。事件类型包括：

* `llm_chunk`：模型的增量输出
* `tool_call`：智能体调用工具时的工具名称和参数
* `tool_result`：工具返回给智能体的结果
* `error`：流式响应开始后发生的结构化错误

流式输出示例，JSON lines 格式：

```json
{"type":"tool_call","name":"retrieve_user_documents","args":{"query":"policy overview"}}
{"type":"tool_result","name":"retrieve_user_documents","content":"...retrieved text..."}
{"type":"llm_chunk","content":"Here is a summary of your policy..."}
```

## 🔄 架构

### 1. 文档摄取与索引

* 使用 PDF、DOCX、TXT 加载器
* 通过 `RecursiveCharacterTextSplitter` 进行文本切块
* 使用 `langchain-postgres` 异步索引到 pgvector，并通过 JSONB 保存元数据

### 2. 检索

* 根据 `thread_id` 和 `user_id` 进行过滤后的语义相似度搜索
* 工具 `retrieve_user_documents` 使用向量存储检索器进行文档检索

### 3. 智能体与生成

* 使用 LangGraph ReAct agent，也就是 `create_react_agent`
* 工具包括文档检索和 Tavily 网页搜索
* 通过 `MODEL_NAMES` 配置可用模型
* 支持端到端流式输出

### 4. 记忆

* 使用 LangGraph Postgres checkpointer，异步存储每个线程的聊天历史
* 删除线程时，会清理对应的 checkpointer 状态以及相关向量文本块

## 🖼️ 截图

### 未登录首页

![home](./screenshots/home.png)

### 已登录首页

![home-authenticated](./screenshots/home-authenticated.png)

## 📝 许可证

本项目基于 [MIT License](./LICENSE) 授权。

## 🤝 贡献

欢迎贡献！请提交 issue 或 pull request。
