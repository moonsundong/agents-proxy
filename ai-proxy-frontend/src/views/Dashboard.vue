<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import * as echarts from "echarts";
import { getOverview, getSavings } from "@/api/stats";
import type { StatsOverview } from "@/types/stats";

const overview = ref<StatsOverview | null>(null);
const loading = ref(true);
const trendDays = ref(7);

const routeChartEl = ref<HTMLDivElement>();
const trendChartEl = ref<HTMLDivElement>();
const routeChart = shallowRef<echarts.ECharts>();
const trendChart = shallowRef<echarts.ECharts>();

const ROUTE_LABELS: Record<string, string> = {
  local: "本地模型",
  cloud: "线上模型",
  manual: "手动指定",
};

function renderRouteChart(dist: Record<string, number>) {
  const data = Object.entries(dist).map(([key, value]) => ({
    name: ROUTE_LABELS[key] ?? key,
    value,
  }));
  routeChart.value?.setOption({
    tooltip: { trigger: "item" },
    legend: { bottom: 0 },
    series: [
      {
        type: "pie",
        radius: ["40%", "70%"],
        label: { formatter: "{b}: {c} ({d}%)" },
        data,
      },
    ],
  });
}

async function renderTrendChart() {
  const trend = await getSavings(trendDays.value);
  const dates = trend.days.map((d) => d.date.slice(5)); // MM-DD
  trendChart.value?.setOption({
    tooltip: { trigger: "axis" },
    legend: { bottom: 0 },
    grid: { left: 60, right: 20, top: 30, bottom: 50 },
    xAxis: { type: "category", data: dates },
    yAxis: { type: "value", name: "tokens" },
    series: [
      {
        name: "原始 tokens",
        type: "bar",
        stack: "total",
        itemStyle: { color: "#c0c4cc" },
        data: trend.days.map((d) => d.original_tokens),
      },
      {
        name: "压缩后 tokens",
        type: "bar",
        stack: "compare",
        itemStyle: { color: "#409eff" },
        data: trend.days.map((d) => d.compressed_tokens),
      },
      {
        name: "节省 tokens",
        type: "line",
        smooth: true,
        itemStyle: { color: "#67c23a" },
        data: trend.days.map((d) => d.saved_tokens),
      },
    ],
  });
}

async function refresh() {
  loading.value = true;
  try {
    overview.value = await getOverview();
    renderRouteChart(overview.value.route_distribution);
    await renderTrendChart();
  } finally {
    loading.value = false;
  }
}

const onResize = () => {
  routeChart.value?.resize();
  trendChart.value?.resize();
};

onMounted(() => {
  if (routeChartEl.value) routeChart.value = echarts.init(routeChartEl.value);
  if (trendChartEl.value) trendChart.value = echarts.init(trendChartEl.value);
  window.addEventListener("resize", onResize);
  refresh();
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  routeChart.value?.dispose();
  trendChart.value?.dispose();
});

const fmtRate = (r: number | null) =>
  r === null ? "—" : `${(r * 100).toFixed(1)}%`;
</script>

<template>
  <div class="page-header">
    <h2>仪表盘</h2>
    <el-button :loading="loading" @click="refresh">刷新</el-button>
  </div>

  <el-row v-loading="loading" :gutter="16">
    <el-col :span="6">
      <el-card shadow="hover">
        <div class="stat-value">{{ overview?.today_requests ?? "—" }}</div>
        <div class="stat-label">今日请求</div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <div class="stat-value">
          {{ overview?.tokens_saved_today?.toLocaleString() ?? "—" }}
        </div>
        <div class="stat-label">今日节省 Tokens</div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <div class="stat-value">
          {{ fmtRate(overview?.today_success_rate ?? null) }}
        </div>
        <div class="stat-label">今日成功率</div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <div class="stat-value">
          {{
            overview?.today_avg_latency_ms !== null && overview
              ? `${overview.today_avg_latency_ms}ms`
              : "—"
          }}
        </div>
        <div class="stat-label">今日平均延迟</div>
      </el-card>
    </el-col>
  </el-row>

  <el-row :gutter="16" style="margin-top: 16px">
    <el-col :span="6">
      <el-card shadow="hover">
        <div class="stat-value">
          {{ overview?.total_requests?.toLocaleString() ?? "—" }}
        </div>
        <div class="stat-label">累计请求</div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <div class="stat-value">
          {{ overview?.tokens_saved_total?.toLocaleString() ?? "—" }}
        </div>
        <div class="stat-label">累计节省 Tokens</div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <div class="stat-value">{{ overview?.today_errors ?? "—" }}</div>
        <div class="stat-label">今日失败请求</div>
      </el-card>
    </el-col>
  </el-row>

  <el-row :gutter="16" style="margin-top: 16px">
    <el-col :span="10">
      <el-card>
        <template #header>今日路由分布</template>
        <div ref="routeChartEl" class="chart" />
      </el-card>
    </el-col>
    <el-col :span="14">
      <el-card>
        <template #header>
          <div class="card-header">
            <span>Token 节省趋势</span>
            <el-radio-group
              v-model="trendDays"
              size="small"
              @change="renderTrendChart"
            >
              <el-radio-button :value="7">7 天</el-radio-button>
              <el-radio-button :value="14">14 天</el-radio-button>
              <el-radio-button :value="30">30 天</el-radio-button>
            </el-radio-group>
          </div>
        </template>
        <div ref="trendChartEl" class="chart" />
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.stat-value {
  font-size: 28px;
  font-weight: 600;
}
.stat-label {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.chart {
  height: 320px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
