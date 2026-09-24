# LLM Agent 代理网关

前后端分离的 LLM 请求代理网关:统一 OpenAI 兼容入口,集成 **Headroom 上下文压缩**节省 Token,内置**智能决策路由**按请求复杂度自动选择本地模型或线上模型,并提供 Web 管理界面。

## 核心功能

| 模块 | 说明 |
| --- | --- |
| 请求转发 | OpenAI 兼容 `POST /v1/chat/completions`,支持 SSE 流式、指数退避重试、按模型熔断 |
| Headroom 压缩 | 压缩策略可配置(开启/关闭/仅工具输出),压缩失败自动降级直通,前后 token 对比落库 |
| 智能决策路由 | 决策模型评估请求复杂度输出置信度:≥ 阈值走本地(省费用),< 阈值走线上(保质量);支持手动覆盖、场景化策略、A/B 分流 |
| 模型管理 | 本地 llama-server / OpenAI / OpenAI 兼容服务的 CRUD、健康检查、默认模型与优先级 |
| 统计监控 | 仪表盘(请求量、节省 token、路由分布、趋势图)+ 全链路请求日志(分页筛选) |
| CC Switch 端点 | 端点 CRUD、JSON 导入/导出、热加载 |

## 技术栈

- **后端**:Python 3.12+ / FastAPI / SQLAlchemy 2.0 异步 / SQLite(开发)· PostgreSQL(生产)/ httpx / headroom-ai
- **前端**:Vue 3 / TypeScript / Vite / Pinia / Vue Router / Element Plus / ECharts
- **部署**:Docker / Docker Compose(前端 Nginx 静态服务 + 反向代理)

## 快速开始(本地开发,Windows)

前置:Python 3.12+、Node.js 20+。

```bash
# Git Bash 一键启动(首次自动建 .venv、装依赖、生成 .env)
./start.sh        # Ctrl+C 停止;或双击 start.bat
./stop.sh         # 兜底清理残留进程
```

- 后端: http://localhost:8300 (Swagger 文档 `/docs`)
- 前端: http://localhost:5188

> npm 官方源较慢时可加镜像:`npm install --registry=https://registry.npmmirror.com`

## Docker Compose 一键部署

```bash
docker compose up -d --build
# 前端 http://localhost:5188,后端 http://localhost:8300
```

SQLite 数据与 HuggingFace 模型缓存分别持久化在 `gateway-data`、`hf-cache` 卷。生产切 PostgreSQL:取消 `docker-compose.yml` 中 `db` 服务与 `GATEWAY_DATABASE_URL` 注释,并给后端依赖加 `asyncpg`。

## 配置

后端配置通过环境变量注入(前缀 `GATEWAY_`),模板见 [api_gateway/.env.example](api_gateway/.env.example),`start.sh` 首次运行会自动复制为 `.env`。常用项:

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `GATEWAY_DATABASE_URL` | `sqlite:///./gateway.db` | 数据库连接 |
| `GATEWAY_REQUEST_TIMEOUT` | `60` | 上游 LLM 调用超时(秒) |
| `GATEWAY_LLM_MAX_RETRIES` | `2` | 失败重试次数(429/5xx/传输错误) |
| `GATEWAY_CIRCUIT_FAILURE_THRESHOLD` | `3` | 连续失败熔断阈值 |
| `GATEWAY_CIRCUIT_COOLDOWN_SECONDS` | `30` | 熔断冷却时间 |
| `GATEWAY_HF_HOME` | (系统默认) | HuggingFace 缓存目录(Kompress 模型权重) |

## API 一览

| 接口 | 说明 |
| --- | --- |
| `POST /v1/chat/completions` | 统一转发入口(`stream:true` 走 SSE);请求体扩展字段 `model`(手动覆盖)、`scenario`(路由场景) |
| `POST /v1/chat/completions/stream` | 流式转发别名 |
| `GET/POST/PUT/DELETE /api/models` | 模型管理,另有 `GET /api/models/{id}/health` |
| `GET/PUT /api/compression/config` | 压缩策略配置 |
| `GET/POST/PUT/DELETE /api/routing/policies` | 路由策略管理 |
| `GET /api/stats/overview` `/logs` `/savings` | 统计概览 / 日志分页 / 节省趋势 |
| `GET/POST/PUT/DELETE /api/cc-switch/endpoints` | CC Switch 端点管理,另有 `/export`、`/import` |
| `GET /health` | 健康检查 |

### 转发示例

```bash
curl -X POST http://localhost:8300/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"你好"}]}'

# 手动指定模型(跳过决策路由)
curl -X POST http://localhost:8300/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"你好"}],"stream":true}'
```

## 项目结构

```
├── api_gateway/            # 后端 FastAPI
│   ├── app/
│   │   ├── clients/        # LLM 客户端 / Headroom 封装
│   │   ├── models/         # SQLAlchemy 模型(5 张表)
│   │   ├── routes/         # API 路由
│   │   ├── services/       # 转发引擎 / 压缩 / 决策路由 / 统计
│   │   └── config/         # 配置与数据库
│   ├── tests/              # pytest(进程内 ASGI 测试)
│   └── Dockerfile
├── ai-proxy-frontend/      # 前端 Vue 3
│   ├── src/views/          # 仪表盘/模型/路由/压缩/日志/设置
│   ├── nginx.conf
│   └── Dockerfile
├── docker-compose.yml
└── start.sh / stop.sh      # 本地一键启停(另有 .bat 双击版)
```

## 测试与质量

```bash
# 后端:41 个单元/接口测试(httpx ASGITransport 进程内,不起真实服务)
cd api_gateway && ../.venv/Scripts/python -m pytest tests/ -q

# 后端 lint
../.venv/Scripts/python -m ruff check app tests

# 前端:类型检查 + 构建 + lint
cd ai-proxy-frontend
npm run build      # vue-tsc + vite build
npm run lint       # eslint
npm run format     # prettier
```

## 已知限制

- Anthropic 原生协议转换未实现(anthropic 类型模型会显式报错);本地 llama-server / OpenAI / OpenAI 兼容服务不受影响
- 熔断器与压缩配置缓存为进程内存态,多实例部署时各自独立(如需共享可引入 Redis)
- 首次执行真实压缩会从 HuggingFace 下载 Kompress 模型权重,失败自动降级直通
