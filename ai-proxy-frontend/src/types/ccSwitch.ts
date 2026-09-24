export interface CCSwitchEndpoint {
  id: number;
  name: string;
  url: string;
  api_key: string | null;
  timeout: number;
  health_check_interval: number;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface CCSwitchEndpointPayload {
  name: string;
  url: string;
  api_key?: string | null;
  timeout?: number;
  health_check_interval?: number;
  is_enabled?: boolean;
}

export interface CCSwitchImportResult {
  imported: number;
  skipped: string[];
}
