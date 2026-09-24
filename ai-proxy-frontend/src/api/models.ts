import client from "./client";
import type {
  LLMModel,
  ModelCreatePayload,
  ModelUpdatePayload,
  HealthCheckResult,
} from "@/types/model";

export const listModels = () =>
  client.get<LLMModel[]>("/api/models").then((r) => r.data);

export const createModel = (data: ModelCreatePayload) =>
  client.post<LLMModel>("/api/models", data).then((r) => r.data);

export const updateModel = (id: number, data: ModelUpdatePayload) =>
  client.put<LLMModel>(`/api/models/${id}`, data).then((r) => r.data);

export const deleteModel = (id: number) => client.delete(`/api/models/${id}`);

export const checkModelHealth = (id: number) =>
  client.get<HealthCheckResult>(`/api/models/${id}/health`).then((r) => r.data);

export const fetchUpstreamModels = (data: {
  base_url: string;
  api_key?: string | null;
  type?: string;
}) =>
  client
    .post<{ models: string[] }>("/api/models/fetch-upstream", data)
    .then((r) => r.data.models);
