"""应用配置:所有敏感信息通过环境变量注入,禁止硬编码。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """网关全局配置,环境变量前缀为 GATEWAY_。"""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="GATEWAY_", extra="ignore"
    )

    app_name: str = "LLM Agent Gateway"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8300
    log_level: str = "INFO"

    database_url: str = "sqlite:///./gateway.db"
    cors_origins: str = "http://localhost:5188"

    # 调用外部 LLM 后端的默认超时(秒)
    request_timeout: float = 60.0

    # 流式转发:上游两次数据块之间的读取超时(秒)。
    # 慢推理模型(如 Kimi)大 prompt 的首字节/停顿可达分钟级,不能用 request_timeout
    stream_read_timeout: float = 300.0

    # 流式转发:上游空闲超过该间隔时,向客户端注入 SSE 注释心跳(秒),0 关闭。
    # codex 等客户端有流空闲超时,心跳可防止其在上游长思考时断开重连
    stream_keepalive_seconds: float = 15.0

    # 转发引擎:重试与熔断
    llm_max_retries: int = 2  # 首次失败后的额外重试次数
    llm_retry_backoff: float = 0.5  # 指数退避基数(秒)
    circuit_failure_threshold: int = 3  # 连续失败多少次后熔断
    circuit_cooldown_seconds: float = 30.0  # 熔断后冷却时间(秒)

    # HuggingFace 缓存目录(Headroom Kompress 模型权重下载位置),
    # 需在 headroom 首次导入前生效;None 表示用系统默认 ~/.cache/huggingface
    hf_home: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
