export type CompressionMode = "on" | "off" | "tools_only";

export interface CompressionConfig {
  id: number;
  mode: CompressionMode;
  compress_user_messages: boolean;
  compress_system_messages: boolean;
  protect_recent: number;
  target_ratio: number | null;
  min_tokens_to_compress: number;
  kompress_model: string | null;
  keep_markers: string | null;
  updated_at: string;
}

export type CompressionConfigUpdatePayload = Partial<
  Omit<CompressionConfig, "id" | "updated_at">
>;
