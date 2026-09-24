<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useModelStore } from "@/stores/models";
import {
  listPolicies,
  createPolicy,
  updatePolicy,
  deletePolicy,
} from "@/api/routing";
import type {
  RoutingPolicy,
  RoutingPolicyCreatePayload,
} from "@/types/routing";

const modelStore = useModelStore();
const policies = ref<RoutingPolicy[]>([]);
const loading = ref(false);

async function fetchPolicies() {
  loading.value = true;
  try {
    policies.value = await listPolicies();
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await modelStore.fetchModels();
  await fetchPolicies();
});

const modelName = (id: number | null) =>
  id === null
    ? "—"
    : (modelStore.models.find((m) => m.id === id)?.name ?? `#${id}`);

// ---------------- 新建/编辑对话框 ----------------
const dialogVisible = ref(false);
const dialogTitle = ref("");
const saving = ref(false);
const editingId = ref<number | null>(null);

const emptyForm = (): RoutingPolicyCreatePayload => ({
  name: "",
  scenario: "default",
  decision_model_id: null,
  decision_content_limit: 8000,
  decision_head_ratio: 0.2,
  decision_tiers: [],
  is_active: true,
});
const form = reactive<RoutingPolicyCreatePayload>(emptyForm());

function openCreate() {
  editingId.value = null;
  Object.assign(form, emptyForm());
  dialogTitle.value = "新增路由策略";
  dialogVisible.value = true;
}

function openEdit(row: RoutingPolicy) {
  editingId.value = row.id;
  Object.assign(form, {
    name: row.name,
    scenario: row.scenario,
    decision_model_id: row.decision_model_id,
    decision_content_limit: row.decision_content_limit,
    decision_head_ratio: row.decision_head_ratio,
    decision_tiers: (row.decision_tiers ?? []).map((t) => ({ ...t })),
    is_active: row.is_active,
  });
  dialogTitle.value = `编辑策略: ${row.name}`;
  dialogVisible.value = true;
}

// ---------------- 置信度梯度区间 ----------------
function addTier() {
  form.decision_tiers = [
    ...(form.decision_tiers ?? []),
    { min_confidence: 0, model_id: null },
  ];
}

function removeTier(index: number) {
  form.decision_tiers = (form.decision_tiers ?? []).filter((_, i) => i !== index);
}

async function save() {
  saving.value = true;
  try {
    // 过滤未选模型的行;空区间传 null,回退到二元阈值分流
    const tiers = (form.decision_tiers ?? []).filter((t) => t.model_id !== null);
    const payload = { ...form, decision_tiers: tiers.length > 0 ? tiers : null };
    if (editingId.value === null) {
      await createPolicy(payload);
      ElMessage.success("策略已创建");
    } else {
      await updatePolicy(editingId.value, payload);
      ElMessage.success("策略已更新");
    }
    dialogVisible.value = false;
    await fetchPolicies();
  } finally {
    saving.value = false;
  }
}

async function remove(row: RoutingPolicy) {
  await ElMessageBox.confirm(`确定删除策略「${row.name}」?`, "删除确认", {
    type: "warning",
  });
  await deletePolicy(row.id);
  ElMessage.success("已删除");
  await fetchPolicies();
}

async function toggleActive(row: RoutingPolicy) {
  await updatePolicy(row.id, { is_active: row.is_active });
  ElMessage.success(row.is_active ? "已启用" : "已停用");
  await fetchPolicies();
}
</script>

<template>
  <div class="page-header">
    <h2>路由策略</h2>
    <el-button type="primary" @click="openCreate">新增策略</el-button>
  </div>

  <el-alert
    type="info"
    :closable="false"
    style="margin-bottom: 16px"
    title="所有请求一律走策略路由:决策模型评估请求简单度并给出评分,按「置信度区间」梯度路由——简单请求走弱模型省成本,复杂请求走强模型保质量。请求中的 model 字段不影响路由决策。"
  />

  <el-table v-loading="loading" :data="policies" stripe>
    <el-table-column prop="name" label="名称" min-width="120" />
    <el-table-column prop="scenario" label="场景" width="100" />
    <el-table-column label="决策模型" min-width="120">
      <template #default="{ row }">{{
        modelName(row.decision_model_id)
      }}</template>
    </el-table-column>
    <el-table-column label="梯度区间" min-width="220">
      <template #default="{ row }">
        <template v-if="row.decision_tiers?.length">
          <el-tag
            v-for="t in [...row.decision_tiers].sort(
              (a, b) => b.min_confidence - a.min_confidence,
            )"
            :key="t.model_id"
            size="small"
            style="margin-right: 4px"
          >
            ≥{{ t.min_confidence.toFixed(2) }} {{ modelName(t.model_id) }}
          </el-tag>
        </template>
        <el-tag v-else type="info">未配置</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="启用" width="70">
      <template #default="{ row }">
        <el-switch :model-value="row.is_active" @change="toggleActive(row)" />
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

  <el-dialog v-model="dialogVisible" :title="dialogTitle" width="560px">
    <el-form :model="form" label-width="120px">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" placeholder="如 默认策略" />
      </el-form-item>
      <el-form-item label="场景">
        <el-input
          v-model="form.scenario"
          placeholder="default / code / chat ..."
        />
        <div class="form-hint">
          请求体中的 scenario 字段用于匹配策略,默认 default
        </div>
      </el-form-item>
      <el-form-item label="决策模型">
        <el-select
          v-model="form.decision_model_id"
          clearable
          placeholder="选择模型"
          style="width: 100%"
        >
          <el-option
            v-for="m in modelStore.models"
            :key="m.id"
            :value="m.id"
            :label="`${m.name} (${m.type})`"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="决策采样上限">
        <el-input-number
          v-model="form.decision_content_limit"
          :min="500"
          :max="200000"
          :step="1000"
        />
        <span class="form-hint">
          字符,仅本地决策模型生效(保评估速度);线上决策模型按其上下文窗口自动推导
        </span>
      </el-form-item>
      <el-form-item label="头部采样占比">
        <el-slider
          v-model="form.decision_head_ratio"
          :min="0"
          :max="1"
          :step="0.05"
          :format-tooltip="(v: number) => `头部 ${(v * 100).toFixed(0)}% / 尾部 ${(100 - v * 100).toFixed(0)}%`"
          show-input
        />
        <div class="form-hint">
          决策内容超长时按头部+尾部截取;尾部是最新用户消息,复杂度判断主要看它
        </div>
      </el-form-item>
      <el-form-item label="置信度区间">
        <div class="tiers-editor">
          <div
            v-for="(tier, i) in form.decision_tiers ?? []"
            :key="i"
            class="tier-row"
          >
            <span class="tier-label">≥</span>
            <el-input-number
              v-model="tier.min_confidence"
              :min="0"
              :max="1"
              :step="0.05"
              style="width: 130px"
            />
            <el-select
              v-model="tier.model_id"
              placeholder="选择模型"
              style="flex: 1"
            >
              <el-option
                v-for="m in modelStore.models"
                :key="m.id"
                :value="m.id"
                :label="`${m.name} (${m.type})`"
              />
            </el-select>
            <el-button size="small" type="danger" @click="removeTier(i)">
              删除
            </el-button>
          </div>
          <el-button size="small" @click="addTier">添加区间</el-button>
          <div class="form-hint">
            置信度 ≥ 下限时路由到对应模型,自动按下限从高到低匹配;命中档模型停用或上下文装不下时顺延下一档。配置区间后上方「置信度阈值」不再生效。如:0.8→本地 / 0.5→deepseek / 0.2→k3 / 0.0→chatgpt
          </div>
        </div>
      </el-form-item>
      <el-form-item label="启用">
        <el-switch v-model="form.is_active" />
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
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.tiers-editor {
  width: 100%;
}
.tier-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.tier-label {
  color: var(--el-text-color-secondary);
}
</style>
