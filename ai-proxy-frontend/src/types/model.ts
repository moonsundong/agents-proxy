export type ModelType = "local" | "openai" | "anthropic" | "openai_compatible";

export interface LLMModel {
  id: number;
  name: string;
  type: ModelType;
  base_url: string;
  api_key: string | null;
  model_id: string;
  context_window: number;
  temperature: number;
  priority: number;
  is_default: boolean;
  is_enabled: boolean;
  health_status: "unknown" | "healthy" | "unhealthy";
  last_health_check: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface ModelCreatePayload {
  name: string;
  type: ModelType;
  base_url: string;
  api_key?: string | null;
  model_id: string;
  context_window?: number;
  temperature?: number;
  priority?: number;
  is_default?: boolean;
  is_enabled?: boolean;
  description?: string | null;
}

export type ModelUpdatePayload = Partial<ModelCreatePayload>;

export interface HealthCheckResult {
  model_id: number;
  status: "healthy" | "unhealthy";
  latency_ms: number | null;
  detail: string | null;
}
