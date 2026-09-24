import client from "./client";
import type {
  CompressionConfig,
  CompressionConfigUpdatePayload,
} from "@/types/compression";

export const getCompressionConfig = () =>
  client.get<CompressionConfig>("/api/compression/config").then((r) => r.data);

export const updateCompressionConfig = (data: CompressionConfigUpdatePayload) =>
  client
    .put<CompressionConfig>("/api/compression/config", data)
    .then((r) => r.data);
