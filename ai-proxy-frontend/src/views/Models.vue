<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useModelStore } from "@/stores/models";
import {
  createModel,
  deleteModel,
  updateModel,
  checkModelHealth,
  fetchUpstreamModels,
} from "@/api/models";
import type { LLMModel, ModelCreatePayload, ModelType } from "@/types/model";

const store = useModelStore();
onMounted(store.fetchModels);

const typeOptions: { value: ModelType; label: string }[] = [
  { value: "local", label: "本地 (llama-server)" },
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "openai_compatible", label: "OpenAI 兼容" },
];

const healthTag = (s: string) =>
  s === "healthy" ? "success" : s === "unhealthy" ? "danger" : "info";

const contextWindowOptions = [
  { value: 8192, label: "8K (8192)" },
  { value: 32768, label: "32K" },
  { value: 65536, label: "64K" },
  { value: 131072, label: "128K" },
  { value: 204800, label: "200K" },
  { value: 262144, label: "256K" },
  { value: 1048576, label: "1M" },
];

// ---------------- 从上游拉取模型 ID ----------------
const upstreamModels = ref<string[]>([]);
const fetchingModels = ref(false);

async function fetchUpstream() {
  if (!form.base_url) {
    ElMessage.warning("请先填写 API 地址");
    return;
  }
  fetchingModels.value = true;
  try {
    upstreamModels.value = await fetchUpstreamModels({
      base_url: form.base_url,
      api_key: form.api_key,
      type: form.type,
    });
    if (upstreamModels.value.length === 0) {
      ElMessage.info("上游返回了空列表,请手动输入模型 ID");
    } else {
      ElMessage.success(`获取到 ${upstreamModels.value.length} 个模型,请下拉选择`);
    }
  } finally {
    fetchingModels.value = false;
  }
}

// ---------------- 新建/编辑对话框 ----------------
const dialogVisible = ref(false);
const dialogTitle = ref("");
const saving = ref(false);
const editingId = ref<number | null>(null);

const emptyForm = (): ModelCreatePayload => ({
  name: "",
  type: "local",
  base_url: "",
  api_key: null,
  model_id: "",
  context_window: 8192,
  temperature: 0.7,
  priority: 0,
  is_default: false,
  is_enabled: true,
  description: null,
});
const form = reactive<ModelCreatePayload>(emptyForm());

function openCreate() {
  editingId.value = null;
  Object.assign(form, emptyForm());
  upstreamModels.value = [];
  dialogTitle.value = "新增模型";
  dialogVisible.value = true;
}

function openEdit(row: LLMModel) {
  editingId.value = row.id;
  Object.assign(form, {
    name: row.name,
    type: row.type,
    base_url: row.base_url,
    api_key: row.api_key,
    model_id: row.model_id,
    context_window: row.context_window,
    temperature: row.temperature,
    priority: row.priority,
    is_default: row.is_default,
    is_enabled: row.is_enabled,
    description: row.description,
  });
  dialogTitle.value = `编辑模型: ${row.name}`;
  upstreamModels.value = [];
  dialogVisible.value = true;
}

function openCopy(row: LLMModel) {
  // 以现有模型为模板新建:名称自动加后缀避免唯一约束冲突,默认标记不带过去
  editingId.value = null;
  Object.assign(form, {
    name: `${row.name}-copy`,
    type: row.type,
    base_url: row.base_url,
    api_key: row.api_key,
    model_id: row.model_id,
    context_window: row.context_window,
    temperature: row.temperature,
    priority: row.priority,
    is_default: false,
    is_enabled: row.is_enabled,
    description: row.description,
  });
  dialogTitle.value = `复制模型: ${row.name}`;
  upstreamModels.value = [];
  dialogVisible.value = true;
}

async function save() {
  saving.value = true;
  try {
    // allow-create 的自定义值是字符串,统一转数字
    form.context_window = Number(form.context_window) || 8192;
    if (editingId.value === null) {
      await createModel(form);
      ElMessage.success("模型已创建");
    } else {
      await updateModel(editingId.value, form);
      ElMessage.success("模型已更新");
    }
    dialogVisible.value = false;
    await store.fetchModels();
  } finally {
    saving.value = false;
  }
}

// ---------------- 行操作 ----------------
async function remove(row: LLMModel) {
  await ElMessageBox.confirm(`确定删除模型「${row.name}」?`, "删除确认", {
    type: "warning",
  });
  await deleteModel(row.id);
  ElMessage.success("已删除");
  await store.fetchModels();
}

async function toggleEnabled(row: LLMModel) {
  await updateModel(row.id, { is_enabled: row.is_enabled });
  ElMessage.success(row.is_enabled ? "已启用" : "已停用");
  await store.fetchModels();
}

const healthChecking = ref<Set<number>>(new Set());
async function healthCheck(row: LLMModel) {
  healthChecking.value.add(row.id);
  try {
    const result = await checkModelHealth(row.id);
    if (result.status === "healthy") {
      ElMessage.success(`${row.name} 健康,延迟 ${result.latency_ms}ms`);
    } else {
      ElMessage.warning(`${row.name} 不健康: ${result.detail ?? "未知原因"}`);
    }
    await store.fetchModels();
  } finally {
    healthChecking.value.delete(row.id);
  }
}
</script>

<template>
  <div class="page-header">
    <h2>模型管理</h2>
    <el-button type="primary" @click="openCreate">新增模型</el-button>
  </div>

  <el-table v-loading="store.loading" :data="store.models" stripe>
    <el-table-column prop="name" label="名称" min-width="130" />
    <el-table-column prop="type" label="类型" width="150" />
    <el-table-column
      prop="base_url"
      label="API 地址"
      min-width="200"
      show-overflow-tooltip
    />
    <el-table-column
      prop="model_id"
      label="模型 ID"
      min-width="130"
      show-overflow-tooltip
    />
    <el-table-column prop="context_window" label="上下文" width="90" />
    <el-table-column label="默认" width="70">
      <template #default="{ row }">
        <el-tag v-if="row.is_default" type="warning">默认</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="健康" width="90">
      <template #default="{ row }">
        <el-tag :type="healthTag(row.health_status)">{{
          row.health_status
        }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="启用" width="70">
      <template #default="{ row }">
        <el-switch :model-value="row.is_enabled" @change="toggleEnabled(row)" />
      </template>
    </el-table-column>
    <el-table-column label="操作" width="290" fixed="right">
      <template #default="{ row }">
        <el-button
          size="small"
          :loading="healthChecking.has(row.id)"
          @click="healthCheck(row)"
          >健康检查</el-button
        >
        <el-button size="small" @click="openEdit(row)">编辑</el-button>
        <el-button size="small" @click="openCopy(row)">复制</el-button>
        <el-button size="small" type="danger" @click="remove(row)"
          >删除</el-button
        >
      </template>
    </el-table-column>
  </el-table>

  <el-dialog v-model="dialogVisible" :title="dialogTitle" width="560px">
    <el-form :model="form" label-width="110px">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" placeholder="如 local-bonsai" />
      </el-form-item>
      <el-form-item label="类型" required>
        <el-select v-model="form.type" style="width: 100%">
          <el-option
            v-for="t in typeOptions"
            :key="t.value"
            :value="t.value"
            :label="t.label"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="API 地址" required>
        <el-input
          v-model="form.base_url"
          placeholder="如 http://127.0.0.1:7070"
        />
      </el-form-item>
      <el-form-item label="API Key">
        <el-input
          v-model="form.api_key"
          type="password"
          show-password
          placeholder="可空"
        />
      </el-form-item>
      <el-form-item label="模型 ID" required>
        <div class="model-id-row">
          <el-select
            v-model="form.model_id"
            filterable
            allow-create
            default-first-option
            placeholder="可输入,或点右侧按钮从上游获取"
            style="flex: 1"
          >
            <el-option v-for="m in upstreamModels" :key="m" :value="m" :label="m" />
          </el-select>
          <el-button :loading="fetchingModels" @click="fetchUpstream">获取</el-button>
        </div>
      </el-form-item>
      <el-form-item label="上下文窗口">
        <el-select
          v-model="form.context_window"
          filterable
          allow-create
          default-first-option
          style="width: 220px"
        >
          <el-option
            v-for="opt in contextWindowOptions"
            :key="opt.value"
            :value="opt.value"
            :label="opt.label"
          />
        </el-select>
        <span class="form-hint">可下拉选择常用档位,也可直接输入数字</span>
      </el-form-item>
      <el-form-item label="温度">
        <el-slider
          v-model="form.temperature"
          :min="0"
          :max="2"
          :step="0.1"
          show-input
        />
      </el-form-item>
      <el-form-item label="优先级">
        <el-input-number v-model="form.priority" :min="0" />
        <span class="form-hint">数值越小优先级越高</span>
      </el-form-item>
      <el-form-item label="默认模型">
        <el-switch v-model="form.is_default" />
        <span class="form-hint">全局唯一,设置后自动取消其他模型的默认标记</span>
      </el-form-item>
      <el-form-item label="启用">
        <el-switch v-model="form.is_enabled" />
      </el-form-item>
      <el-form-item label="描述">
        <el-input v-model="form.description" type="textarea" :rows="2" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.form-hint {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.model-id-row {
  display: flex;
  gap: 8px;
  width: 100%;
}
</style>
