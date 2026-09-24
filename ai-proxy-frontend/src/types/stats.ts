export interface RequestLog {
  id: number;
  request_id: string;
  model_id: number | null;
  model_name: string | null;
  route: "local" | "cloud" | "manual" | null;
  confidence: number | null;
  route_reason: string | null;
  request_excerpt: string | null;
  original_tokens: number | null;
  compressed_tokens: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  latency_ms: number | null;
  status: "success" | "error" | "cancelled";
  error: string | null;
  created_at: string;
}

export interface LogPage {
  total: number;
  page: number;
  page_size: number;
  items: RequestLog[];
}

export interface StatsOverview {
  today_requests: number;
  today_errors: number;
  today_success_rate: number | null;
  today_avg_latency_ms: number | null;
  total_requests: number;
  tokens_saved_today: number;
  tokens_saved_total: number;
  route_distribution: Record<string, number>;
}

export interface DailySavings {
  date: string;
  requests: number;
  original_tokens: number;
  compressed_tokens: number;
  saved_tokens: number;
}

export interface SavingsTrend {
  days: DailySavings[];
}

export interface LogQuery {
  page: number;
  page_size: number;
  route?: string;
  status?: string;
  model_name?: string;
  days?: number;
}
