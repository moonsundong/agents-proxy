import client from "./client";
import type {
  LogPage,
  LogQuery,
  SavingsTrend,
  StatsOverview,
} from "@/types/stats";

export const getOverview = () =>
  client.get<StatsOverview>("/api/stats/overview").then((r) => r.data);

export const getLogs = (query: LogQuery) =>
  client.get<LogPage>("/api/stats/logs", { params: query }).then((r) => r.data);

export const getSavings = (days = 7) =>
  client
    .get<SavingsTrend>("/api/stats/savings", { params: { days } })
    .then((r) => r.data);
