import client from "./client";
import type {
  RoutingPolicy,
  RoutingPolicyCreatePayload,
  RoutingPolicyUpdatePayload,
} from "@/types/routing";

export const listPolicies = () =>
  client.get<RoutingPolicy[]>("/api/routing/policies").then((r) => r.data);

export const createPolicy = (data: RoutingPolicyCreatePayload) =>
  client.post<RoutingPolicy>("/api/routing/policies", data).then((r) => r.data);

export const updatePolicy = (id: number, data: RoutingPolicyUpdatePayload) =>
  client
    .put<RoutingPolicy>(`/api/routing/policies/${id}`, data)
    .then((r) => r.data);

export const deletePolicy = (id: number) =>
  client.delete(`/api/routing/policies/${id}`);
