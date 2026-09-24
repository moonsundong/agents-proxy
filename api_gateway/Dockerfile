# 后端镜像:FastAPI + uvicorn
FROM python:3.12-slim

WORKDIR /app

# 先装依赖,利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite 数据文件挂到 /data,便于卷持久化
ENV GATEWAY_DATABASE_URL=sqlite:////data/gateway.db \
    GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=8300
VOLUME ["/data"]

EXPOSE 8300
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
