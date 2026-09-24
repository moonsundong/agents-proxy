<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  listEndpoints,
  createEndpoint,
  updateEndpoint,
  deleteEndpoint,
  exportEndpoints,
  importEndpoints,
} from "@/api/ccSwitch";
import type {
  CCSwitchEndpoint,
  CCSwitchEndpointPayload,
} from "@/types/ccSwitch";

const endpoints = ref<CCSwitchEndpoint[]>([]);
const loading = ref(false);

async function fetchEndpoints() {
  loading.value = true;
  try {
    endpoints.value = await listEndpoints();
  } finally {
    loading.value = false;
  }
}

onMounted(fetchEndpoints);

// ---------------- 新建/编辑对话框 ----------------
const dialogVisible = ref(false);
const dialogTitle = ref("");
const saving = ref(false);
const editingId = ref<number | null>(null);

const emptyForm = (): CCSwitchEndpointPayload => ({
  name: "",
  url: "",
  api_key: null,
  timeout: 30,
  health_check_interval: 60,
  is_enabled: true,
});
const form = reactive<CCSwitchEndpointPayload>(emptyForm());

function openCreate() {
  editingId.value = null;
  Object.assign(form, emptyForm());
  dialogTitle.value = "新增端点";
  dialogVisible.value = true;
}

function openEdit(row: CCSwitchEndpoint) {
  editingId.value = row.id;
  Object.assign(form, {
    name: row.name,
    url: row.url,
    api_key: row.api_key,
    timeout: row.timeout,
    health_check_interval: row.health_check_interval,
    is_enabled: row.is_enabled,
  });
  dialogTitle.value = `编辑端点: ${row.name}`;
  dialogVisible.value = true;
}

async function save() {
  saving.value = true;
  try {
    if (editingId.value === null) {
      await createEndpoint(form);
      ElMessage.success("端点已创建");
    } else {
      await updateEndpoint(editingId.value, form);
      ElMessage.success("端点已更新");
    }
    dialogVisible.value = false;
    await fetchEndpoints();
  } finally {
    saving.value = false;
  }
}

async function remove(row: CCSwitchEndpoint) {
  await ElMessageBox.confirm(`确定删除端点「${row.name}」?`, "删除确认", {
    type: "warning",
  });
  await deleteEndpoint(row.id);
  ElMessage.success("已删除");
  await fetchEndpoints();
}

async function toggleEnabled(row: CCSwitchEndpoint) {
  await updateEndpoint(row.id, { is_enabled: row.is_enabled });
  ElMessage.success(row.is_enabled ? "已启用" : "已停用");
  await fetchEndpoints();
}

// ---------------- 导入/导出 ----------------
async function doExport() {
  const data = await exportEndpoints();
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "cc-switch-endpoints.json";
  link.click();
  URL.revokeObjectURL(link.href);
  ElMessage.success(`已导出 ${data.length} 个端点`);
}

const importInput = ref<HTMLInputElement>();

function triggerImport() {
  importInput.value?.click();
}

async function onImportFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  try {
    const items = JSON.parse(await file.text());
    if (!Array.isArray(items)) throw new Error("文件内容必须是 JSON 数组");
    const result = await importEndpoints(items);
    ElMessage.success(
      `导入 ${result.imported} 个端点` +
        (result.skipped.length ? `,跳过重名 ${result.skipped.length} 个` : ""),
    );
    await fetchEndpoints();
  } catch (e) {
    ElMessage.error(
      `导入失败: ${e instanceof Error ? e.message : "文件格式错误"}`,
    );
  } finally {
    input.value = "";
  }
}
</script>

<template>
  <div class="page-header">
    <h2>系统设置</h2>
    <div>
      <el-button @click="triggerImport">导入 JSON</el-button>
      <el-button @click="doExport">导出 JSON</el-button>
      <el-button type="primary" @click="openCreate">新增端点</el-button>
    </div>
  </div>

  <el-alert
    type="info"
    :closable="false"
    style="margin-bottom: 16px"
    title="CC Switch 端点配置:管理接管 Codex 桌面版等客户端的上游端点,改动即时生效(热加载)。"
  />

  <input
    ref="importInput"
    type="file"
    accept=".json"
    style="display: none"
    @change="onImportFile"
  />

  <el-table v-loading="loading" :data="endpoints" stripe>
    <el-table-column prop="name" label="名称" min-width="140" />
    <el-table-column
      prop="url"
      label="URL"
      min-width="220"
      show-overflow-tooltip
    />
    <el-table-column label="API Key" width="110">
      <template #default="{ row }">
        <el-tag v-if="row.api_key" type="warning">已配置</el-tag>
        <span v-else>—</span>
      </template>
    </el-table-column>
    <el-table-column prop="timeout" label="超时 (s)" width="90" />
    <el-table-column label="健康检查间隔" width="120">
      <template #default="{ row }">{{ row.health_check_interval }}s</template>
    </el-table-column>
    <el-table-column label="启用" width="70">
      <template #default="{ row }">
        <el-switch :model-value="row.is_enabled" @change="toggleEnabled(row)" />
      </template>
    </el-table-column>
    <el-table-column label="操作" width="150" fixed="right">
      <template #default="{ row }">
        <el-button size="small" @click="openEdit(row)">编辑</el-button>
        <el-button size="small" type="danger" @click="remove(row)"
          >删除</el-button
        >
      </template>
    </el-table-column>
  </el-table>

  <el-dialog v-model="dialogVisible" :title="dialogTitle" width="520px">
    <el-form :model="form" label-width="130px">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" placeholder="如 codex-desktop" />
      </el-form-item>
      <el-form-item label="URL" required>
        <el-input v-model="form.url" placeholder="如 http://127.0.0.1:8317" />
      </el-form-item>
      <el-form-item label="API Key">
        <el-input
          v-model="form.api_key"
          type="password"
          show-password
          placeholder="可空"
        />
      </el-form-item>
      <el-form-item label="超时时间 (秒)">
        <el-input-number v-model="form.timeout" :min="1" :max="300" />
      </el-form-item>
      <el-form-item label="健康检查间隔 (秒)">
        <el-input-number
          v-model="form.health_check_interval"
          :min="5"
          :max="3600"
        />
      </el-form-item>
      <el-form-item label="启用">
        <el-switch v-model="form.is_enabled" />
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
</style>
