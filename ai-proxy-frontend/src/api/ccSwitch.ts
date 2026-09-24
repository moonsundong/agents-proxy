import client from "./client";
import type {
  CCSwitchEndpoint,
  CCSwitchEndpointPayload,
  CCSwitchImportResult,
} from "@/types/ccSwitch";

const BASE = "/api/cc-switch/endpoints";

export const listEndpoints = () =>
  client.get<CCSwitchEndpoint[]>(BASE).then((r) => r.data);

export const createEndpoint = (data: CCSwitchEndpointPayload) =>
  client.post<CCSwitchEndpoint>(BASE, data).then((r) => r.data);

export const updateEndpoint = (
  id: number,
  data: Partial<CCSwitchEndpointPayload>,
) => client.put<CCSwitchEndpoint>(`${BASE}/${id}`, data).then((r) => r.data);

export const deleteEndpoint = (id: number) => client.delete(`${BASE}/${id}`);

export const exportEndpoints = () =>
  client.get<CCSwitchEndpointPayload[]>(`${BASE}/export`).then((r) => r.data);

export const importEndpoints = (items: CCSwitchEndpointPayload[]) =>
  client
    .post<CCSwitchImportResult>(`${BASE}/import`, items)
    .then((r) => r.data);
