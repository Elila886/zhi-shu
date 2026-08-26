# 🤖 LangGraph RAG Agent

一个基于 **FastAPI** 和 **LangGraph** 构建的智能体式检索增强生成（RAG）系统，支持流式响应、**PostgreSQL + pgvector** 向量存储，以及现代化的 **Streamlit** 用户界面。该系统支持用户认证、带有持久记忆的多轮线程对话，并通过 LangGraph Postgres checkpointer 实现记忆持久化，同时支持工具增强推理，包括文档检索和网页搜索。

## 🚀 功能特性

* **基于 LangGraph 的智能体式 RAG**：使用 ReAct 风格的智能体，并配备文档检索和网页搜索工具
* **端到端流式响应**：从后端到 Streamlit 前端界面实现实时 token 流式输出
* **线程式对话**：每个用户拥有独立的对话线程，并通过 Postgres checkpointer 持久化历史记录
* **PostgreSQL + pgvector**：用于存储向量，并对用户上传的文档进行语义检索
* **认证与 JWT**：支持注册、登录、刷新 token；用户之间的线程和文档相互隔离
* **文档摄取**：支持 PDF、DOCX 和 TXT 文件，并提供文本切块与异步索引
* **工具能力**：内置 `retrieve_user_documents` 文档检索工具，以及 Tavily 网页搜索集成
* **异步优先的后端**：FastAPI + SQLAlchemy 2.0 async，具备生产级日志和健康检查

## 💻 技术栈

* **后端**：FastAPI、LangGraph、LangChain、SQLAlchemy、Pydantic v2
* **向量存储**：PostgreSQL + pgvector，通过 `langchain-postgres` 使用
* **Checkpointer**：LangGraph Postgres Checkpointer，异步版本
* **前端**：Streamlit
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

* 后端 API：`http://localhost:8000/api/v1`
* API 文档：`http://localhost:8000/api/v1/docs`
* 前端界面：`http://localhost:8501`

注意：

* `pgvector/pgvector:pg16` 镜像已经包含 `vector` 扩展。如果你使用自己的 Postgres，请确保已经启用：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

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

### 2）前端：Streamlit

```bash
cd frontend
python -m venv .venv
.venv/Scripts/activate     # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
streamlit run gui/main.py
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

数据库与向量存储：

* `POSTGRES_HOST`，例如本地运行时使用 `127.0.0.1`，Docker 中使用 `postgres`
* `POSTGRES_PORT`，例如 `5432`
* `POSTGRES_USER`，例如 `postgres`
* `POSTGRES_PASSWORD`，例如 `test`
* `POSTGRES_DATABASE`，例如 `langgraph_db`
* `PGVECTOR_COLLECTION_NAME`，例如 `my_collection`

前端：

* `BACKEND_BASE_URL`，例如本地运行时使用 `http://127.0.0.1:8000/api/v1`

示例配置可以在 `env.example` 中找到。

## 🧩 API 概览

基础 URL：`/api/v1`

认证：

* `POST /auth/signup`
* `POST /auth/login`
* `GET /auth/logout`
* `GET /auth/refresh-token`

用户：

* `GET /users/me`
* `PUT /users/user-profile/{user_id}`
* `DELETE /users/user-profile/{user_id}`

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

* `POST /chat/`：公开流式聊天，不使用工具和记忆
* `POST /chat/{thread_id}`：带认证的流式智能体聊天，支持工具和记忆
* `GET /chat/{thread_id}`：获取持久化的聊天历史

API 文档：

* Swagger UI：`http://localhost:8000/api/v1/docs`
* ReDoc：`http://localhost:8000/api/v1/redoc`

## 📡 流式协议

两个聊天接口都会以换行分隔的 JSON 事件形式进行流式输出。事件类型包括：

* `llm_chunk`：模型的增量输出
* `tool_call`：智能体调用工具时的工具名称和参数
* `tool_result`：工具返回给智能体的结果

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
