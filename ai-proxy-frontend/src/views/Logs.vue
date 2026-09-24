<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useModelStore } from "@/stores/models";
import { getLogs } from "@/api/stats";
import type { RequestLog } from "@/types/stats";

const modelStore = useModelStore();
const logs = ref<RequestLog[]>([]);
const total = ref(0);
const loading = ref(false);

const filters = reactive({
  route: "" as "" | "local" | "cloud" | "manual",
  status: "" as "" | "success" | "error" | "cancelled",
  model_name: "",
  days: 7 as number | undefined,
});
const page = ref(1);
const pageSize = ref(20);

async function fetchLogs() {
  loading.value = true;
  try {
    const data = await getLogs({
      page: page.value,
      page_size: pageSize.value,
      route: filters.route || undefined,
      status: filters.status || undefined,
      model_name: filters.model_name || undefined,
      days: filters.days,
    });
    logs.value = data.items;
    total.value = data.total;
  } finally {
    loading.value = false;
  }
}

function search() {
  page.value = 1;
  fetchLogs();
}

onMounted(async () => {
  await modelStore.fetchModels();
  await fetchLogs();
});

const routeTag = (r: string | null) =>
  r === "local"
    ? "success"
    : r === "cloud"
      ? "primary"
      : r === "manual"
        ? "warning"
        : "info";

const routeLabel = (r: string | null) =>
  r === "local"
    ? "本地"
    : r === "cloud"
      ? "线上"
      : r === "manual"
        ? "手动"
        : (r ?? "—");

const savedRate = (row: RequestLog) => {
  if (!row.original_tokens || row.compressed_tokens === null) return "—";
  const saved = row.original_tokens - row.compressed_tokens;
  return `${saved} (${((saved / row.original_tokens) * 100).toFixed(0)}%)`;
};

const fmtTime = (iso: string) => new Date(iso + "Z").toLocaleString();

const statusTag = (s: string) =>
  s === "success" ? "success" : s === "cancelled" ? "warning" : "danger";

const statusLabel = (s: string) =>
  s === "success" ? "成功" : s === "cancelled" ? "已取消" : "失败";
</script>

<template>
  <h2>请求日志</h2>

  <el-form inline class="filter-bar">
    <el-form-item label="路由">
      <el-select
        v-model="filters.route"
        clearable
        placeholder="全部"
        style="width: 110px"
      >
        <el-option value="local" label="本地" />
        <el-option value="cloud" label="线上" />
        <el-option value="manual" label="手动" />
      </el-select>
    </el-form-item>
    <el-form-item label="状态">
      <el-select
        v-model="filters.status"
        clearable
        placeholder="全部"
        style="width: 110px"
      >
        <el-option value="success" label="成功" />
        <el-option value="error" label="失败" />
        <el-option value="cancelled" label="已取消" />
      </el-select>
    </el-form-item>
    <el-form-item label="模型">
      <el-select
        v-model="filters.model_name"
        clearable
        placeholder="全部"
        style="width: 160px"
      >
        <el-option
          v-for="m in modelStore.models"
          :key="m.id"
          :value="m.name"
          :label="m.name"
        />
      </el-select>
    </el-form-item>
    <el-form-item label="时间范围">
      <el-select v-model="filters.days" style="width: 120px">
        <el-option :value="1" label="今天" />
        <el-option :value="7" label="最近 7 天" />
        <el-option :value="30" label="最近 30 天" />
        <el-option :value="undefined" label="全部" />
      </el-select>
    </el-form-item>
    <el-form-item>
      <el-button type="primary" :loading="loading" @click="search"
        >查询</el-button
      >
    </el-form-item>
  </el-form>

  <el-table v-loading="loading" :data="logs" stripe>
    <el-table-column type="expand">
      <template #default="{ row }">
        <div class="detail">
          <p><b>请求 ID:</b> {{ row.request_id }}</p>
          <p><b>路由原因:</b> {{ row.route_reason ?? "—" }}</p>
          <div v-if="row.request_excerpt" class="excerpt">
            <b>请求摘要(原始用户消息):</b>
            <pre>{{ row.request_excerpt }}</pre>
          </div>
          <p>
            <b>用量:</b> prompt {{ row.prompt_tokens ?? "—" }} / completion
            {{ row.completion_tokens ?? "—" }}
          </p>
          <p v-if="row.error" class="error-text">
            <b>错误:</b> {{ row.error }}
          </p>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="时间" width="170">
      <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
    </el-table-column>
    <el-table-column prop="model_name" label="模型" min-width="120" />
    <el-table-column label="路由" width="80">
      <template #default="{ row }">
        <el-tag :type="routeTag(row.route)">{{ routeLabel(row.route) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="置信度" width="80">
      <template #default="{ row }">
        {{ row.confidence !== null ? row.confidence.toFixed(2) : "—" }}
      </template>
    </el-table-column>
    <el-table-column label="压缩前/后" width="120">
      <template #default="{ row }">
        {{ row.original_tokens ?? "—" }} / {{ row.compressed_tokens ?? "—" }}
      </template>
    </el-table-column>
    <el-table-column label="节省" width="110">
      <template #default="{ row }">{{ savedRate(row) }}</template>
    </el-table-column>
    <el-table-column label="耗时" width="90">
      <template #default="{ row }">
        {{ row.latency_ms !== null ? `${row.latency_ms}ms` : "—" }}
      </template>
    </el-table-column>
    <el-table-column label="状态" width="90">
      <template #default="{ row }">
        <el-tag :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
      </template>
    </el-table-column>
  </el-table>

  <el-pagination
    v-model:current-page="page"
    v-model:page-size="pageSize"
    :total="total"
    :page-sizes="[10, 20, 50, 100]"
    layout="total, sizes, prev, pager, next"
    style="margin-top: 16px; justify-content: flex-end"
    @change="fetchLogs"
  />
</template>

<style scoped>
.filter-bar {
  margin-bottom: 8px;
}
.detail {
  padding: 8px 16px;
}
.excerpt pre {
  margin: 6px 0 0;
  padding: 8px 12px;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}
.error-text {
  color: var(--el-color-danger);
}
</style>
