<div align="center">

# 🚀 LLM Agent Gateway

**一个帮你省 Token、省钱的 LLM 智能代理网关**

把所有 LLM 请求统一收口：先压缩上下文省 Token，再按复杂度自动路由——
简单的活交给本地免费模型，难的活才花 API 的钱。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](api_gateway/requirements.txt)
[![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)](ai-proxy-frontend/package.json)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)](api_gateway/app/main.py)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](docker-compose.yml)

[快速开始](#-快速开始一键) · [工作原理](#-工作原理) · [配置说明](#%EF%B8%8F-配置说明) · [使用场景](#-使用场景) · [FAQ](#-faq)

</div>

---

## 💡 一句话说明

你写代码、用 AI 工具时，每个请求都要花钱（API 调用费）或花时间（等模型回复）。
这个网关夹在**你的工具**和**大模型**中间，自动做两件事：

1. **🗜️ 压缩** — 把冗长的上下文（比如几十轮对话、大段工具输出）压小再发给模型，**直接省 Token 费**
2. **🧠 路由** — 先让一个轻量"决策模型"判断这题难不难：**简单 → 本地模型（免费）**，**难 → 线上大模型（保质量）**

> 所有兼容 OpenAI API 的工具（Codex、Claude Code、Chatbox、自研脚本……）只需把请求地址改成网关地址，立刻生效，**工具本身零改动**。

---

## ✨ 核心功能

| 功能 | 说明 |
| --- | --- |
| 🔌 **OpenAI 兼容** | 标准 `POST /v1/chat/completions`,支持 SSE 流式,任何 OpenAI 兼容客户端即插即用 |
| 🗜️ **Headroom 压缩** | 上下文压缩引擎,策略可配(全开/关闭/仅压缩工具输出),压缩失败自动降级直通,**不影响可用性** |
| 🧠 **智能决策路由** | 决策模型输出置信度:≥ 阈值走本地,< 阈值走线上;支持手动指定模型、场景化策略、A/B 分流 |
| 🛡️ **稳定性** | 指数退避重试 + 按模型熔断,上游抽风时自动保护 |
| 📊 **可视化仪表盘** | 请求量、节省 Token 数、路由分布、趋势图,一目了然 |
| 🎛️ **Web 管理界面** | 模型 CRUD、健康检查、压缩策略、路由策略、全链路请求日志,全部点鼠标搞定 |
| 🔄 **CC Switch 端点管理** | 端点配置 CRUD、JSON 导入导出、热加载 |

## 🏗️ 工作原理

```
 你的工具(Codex / Claude Code / 脚本…)
        │  OpenAI 兼容请求
        ▼
┌─────────────────────────────────────┐
│        LLM Agent Gateway            │
│                                     │
│  ① Headroom 压缩 ──► 上下文变小     │
│         │                           │
│  ② 决策模型评估 ──► 这题难吗?       │
│         │                           │
│    ┌────┴────┐                      │
│    ▼         ▼                      │
│  简单       困难                    │
└────┼─────────┼──────────────────────┘
     ▼         ▼
 本地模型    线上模型
 (免费)    (GPT / Claude…)
```

整个链路透明:每次请求压缩前/后的 Token 数、路由决策、耗时都会落库,仪表盘可查。

---

## 🚀 快速开始(一键)

**前置要求**:Python 3.12+、Node.js 20+(Windows / macOS / Linux 均可)

```bash
git clone https://github.com/moonsundong/agents-proxy.git
cd agents-proxy
./start.sh        # macOS / Linux / Git Bash
# Windows 用户也可以直接双击 start.bat
```

**就这一步。** 脚本会自动:创建虚拟环境 → 安装依赖 → 生成配置文件 → 启动前后端 → 自检。

| 服务 | 地址 |
| --- | --- |
| 🖥️ Web 管理界面 | http://localhost:5188 |
| ⚙️ 后端 API | http://localhost:8300 |
| 📖 Swagger 文档 | http://localhost:8300/docs |

停止:`./stop.sh`(或 `stop.bat`)。

> 💡 npm 官方源慢的话:`npm install --registry=https://registry.npmmirror.com`

## 🐳 Docker 一键部署

不想装 Python/Node?有 Docker 就行:

```bash
docker compose up -d --build
```

前端 http://localhost:5188,后端 http://localhost:8300。SQLite 数据和 HuggingFace 模型缓存分别持久化在 `gateway-data`、`hf-cache` 卷里,重启不丢。

---

## 🎮 使用场景

### 场景 1:接入 Codex / Claude Code(省钱主力场景)

1. 打开 http://localhost:5188 → **模型管理**:
   - 新增本地模型:`base_url=http://127.0.0.1:7070`(你的 llama-server),类型 `local`,设为默认
   - 新增线上模型(OpenAI 或兼容服务),填 API Key
2. **路由策略** → 新增:选决策模型 / 本地模型 / 线上模型,阈值 `0.7`
3. 在 CC Switch(或工具的设置里)把供应商指向网关:
   - **Base URL**:`http://localhost:8300/v1`
   - **API Key**:任意占位值(本地网关目前不做鉴权)
   - 协议选 **OpenAI 兼容(Chat Completions)**

之后你所有 AI 编码请求都会自动走「压缩 → 决策 → 路由」链路。

### 场景 2:自己的脚本 / 应用调用

把原来的 `https://api.openai.com/v1` 换成网关地址即可:

```bash
curl -X POST http://localhost:8300/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"你好"}]}'

# 想强制用某个模型(跳过智能路由):
curl -X POST http://localhost:8300/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"你好"}],"stream":true}'
```

Python / JS 的 OpenAI SDK 同理,只改 `base_url`:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8300/v1", api_key="任意占位")
resp = client.chat.completions.create(
    model="auto",  # 交给网关智能路由
    messages=[{"role": "user", "content": "你好"}],
)
```

---

## ⚙️ 配置说明

所有配置都是环境变量(前缀 `GATEWAY_`),`start.sh` 首次运行会自动从 [api_gateway/.env.example](api_gateway/.env.example) 生成 `api_gateway/.env`,按需修改后重启生效。

### 服务与日志

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `GATEWAY_PORT` | `8300` | 后端端口 |
| `GATEWAY_HOST` | `0.0.0.0` | 监听地址 |
| `GATEWAY_LOG_LEVEL` | `INFO` | 日志级别 |
| `GATEWAY_DEBUG` | `false` | 调试模式 |
| `GATEWAY_CORS_ORIGINS` | `http://localhost:5188` | 允许的前端跨域来源 |

### 数据库

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `GATEWAY_DATABASE_URL` | `sqlite:///./gateway.db` | 开发用 SQLite,开箱即用;生产可切 PostgreSQL:`postgresql+asyncpg://user:pass@host:5432/gateway`(需加装 `asyncpg`) |

### 上游调用 / 重试 / 熔断

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `GATEWAY_REQUEST_TIMEOUT` | `60` | 上游 LLM 调用超时(秒) |
| `GATEWAY_LLM_MAX_RETRIES` | `2` | 失败重试次数(429/5xx/传输错误) |
| `GATEWAY_LLM_RETRY_BACKOFF` | `0.5` | 重试退避基数(秒,指数增长) |
| `GATEWAY_CIRCUIT_FAILURE_THRESHOLD` | `3` | 连续失败多少次后熔断该模型 |
| `GATEWAY_CIRCUIT_COOLDOWN_SECONDS` | `30` | 熔断冷却时间,到期自动半开试探 |

### 压缩引擎

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `GATEWAY_HF_HOME` | (系统默认 `~/.cache/huggingface`) | Kompress 模型权重缓存目录;Windows 建议改到非系统盘,如 `D:\hf-cache` |

> 压缩策略(开启/关闭/仅工具输出)不在环境变量里,直接在 Web 界面的「压缩配置」页调整,实时生效。

### 路由阈值怎么调?

阈值是决策模型输出的置信度分界线(0~1):

| 阈值 | 效果 |
| --- | --- |
| 调高(如 `0.9`) | 更多请求走线上 → 质量优先,省钱少 |
| 调低(如 `0.5`) | 更多请求走本地 → 省钱优先,质量让步 |
| `0.7` | 推荐起点,观察仪表盘的路由分布再微调 |

---

## 📸 界面预览

> 截图待补充 —— 欢迎提 PR 或直接看本地 http://localhost:5188

<!--
把截图放到 docs/images/ 后取消注释:
![仪表盘](docs/images/dashboard.png)
![模型管理](docs/images/models.png)
![路由策略](docs/images/routing.png)
![请求日志](docs/images/logs.png)
-->

---

## 📚 API 一览

| 接口 | 说明 |
| --- | --- |
| `POST /v1/chat/completions` | 统一转发入口(`stream:true` 走 SSE);扩展字段 `model`(手动覆盖)、`scenario`(路由场景) |
| `POST /v1/chat/completions/stream` | 流式转发别名 |
| `GET/POST/PUT/DELETE /api/models` | 模型管理,另有 `GET /api/models/{id}/health` |
| `GET/PUT /api/compression/config` | 压缩策略配置 |
| `GET/POST/PUT/DELETE /api/routing/policies` | 路由策略管理 |
| `GET /api/stats/overview` `/logs` `/savings` | 统计概览 / 日志分页 / 节省趋势 |
| `GET/POST/PUT/DELETE /api/cc-switch/endpoints` | CC Switch 端点管理,另有 `/export`、`/import` |
| `GET /health` | 健康检查 |

完整交互式文档:启动后访问 http://localhost:8300/docs

## 🧱 技术栈与项目结构

- **后端**:Python 3.12+ / FastAPI / SQLAlchemy 2.0 异步 / SQLite(开发)· PostgreSQL(生产)/ httpx / headroom-ai
- **前端**:Vue 3 / TypeScript / Vite / Pinia / Vue Router / Element Plus / ECharts
- **部署**:Docker Compose(前端 Nginx 静态服务 + 反向代理)

```
├── api_gateway/            # 后端 FastAPI
│   ├── app/
│   │   ├── clients/        # LLM 客户端 / Headroom 封装
│   │   ├── models/         # SQLAlchemy 模型(5 张表)
│   │   ├── routes/         # API 路由
│   │   ├── services/       # 转发引擎 / 压缩 / 决策路由 / 统计
│   │   └── config/         # 配置与数据库
│   ├── tests/              # pytest(进程内 ASGI 测试,41 个用例)
│   └── Dockerfile
├── ai-proxy-frontend/      # 前端 Vue 3
│   ├── src/views/          # 仪表盘/模型/路由/压缩/日志/设置
│   ├── nginx.conf
│   └── Dockerfile
├── docker-compose.yml
└── start.sh / stop.sh      # 一键启停(另有 .bat 双击版)
```

## 🛠️ 开发

```bash
# 后端测试(进程内,不起真实服务)
cd api_gateway && ../.venv/Scripts/python -m pytest tests/ -q

# 后端 lint
../.venv/Scripts/python -m ruff check app tests

# 前端:类型检查 + 构建 + lint
cd ai-proxy-frontend
npm run build && npm run lint
```

## ❓ FAQ

**Q: 一定要装本地模型才能用吗?**
不。不配本地模型时,把路由策略的本地/线上都指到线上模型(或干脆手动指定模型),网关就只提供「压缩 + 统一入口 + 监控」能力,照样省 Token。

**Q: 压缩会把内容压丢吗?**
压缩失败会自动降级为原文直通,请求不会失败;压缩前后的 Token 数都会记录,可以在仪表盘核对效果。

**Q: 网关有鉴权吗?能暴露到公网吗?**
目前为本地回环设计,不校验 API Key。要公网部署请自行在前面加鉴权层(如 Nginx basic auth / API 网关),并收紧 `GATEWAY_CORS_ORIGINS`。

**Q: 支持 Anthropic 原生协议吗?**
暂不支持,请求 anthropic 类型模型会显式报错;OpenAI 及 OpenAI 兼容服务(含本地 llama-server)不受影响。

## ⚠️ 已知限制

- 熔断器与压缩配置缓存为进程内存态,多实例部署时各自独立(如需共享可引入 Redis)
- 首次执行真实压缩会从 HuggingFace 下载 Kompress 模型权重(失败自动降级直通)

## 🤝 贡献

欢迎 Issue 和 PR!无论是 bug 修复、新功能、文档改进还是界面截图,都非常感谢。

## 📄 开源协议

[MIT License](LICENSE) — 随意用,改,商用,只需保留版权声明。

---

<div align="center">

**如果觉得有用,点个 ⭐ Star 支持一下!**

</div>
