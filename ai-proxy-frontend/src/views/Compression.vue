<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import {
  getCompressionConfig,
  updateCompressionConfig,
} from "@/api/compression";
import type { CompressionMode } from "@/types/compression";

const loading = ref(false);
const saving = ref(false);

const form = reactive({
  mode: "on" as CompressionMode,
  compress_user_messages: false,
  compress_system_messages: true,
  protect_recent: 4,
  target_ratio: null as number | null,
  min_tokens_to_compress: 250,
  kompress_model: null as string | null,
  keep_markers: null as string | null,
});

const ratioEnabled = ref(false);
const kompressDisabled = ref(false);

onMounted(async () => {
  loading.value = true;
  try {
    const cfg = await getCompressionConfig();
    Object.assign(form, {
      mode: cfg.mode,
      compress_user_messages: cfg.compress_user_messages,
      compress_system_messages: cfg.compress_system_messages,
      protect_recent: cfg.protect_recent,
      target_ratio: cfg.target_ratio,
      min_tokens_to_compress: cfg.min_tokens_to_compress,
      kompress_model: cfg.kompress_model,
      keep_markers: cfg.keep_markers,
    });
    ratioEnabled.value = cfg.target_ratio !== null;
    kompressDisabled.value = cfg.kompress_model === "disabled";
  } finally {
    loading.value = false;
  }
});

async function save() {
  saving.value = true;
  try {
    await updateCompressionConfig({
      ...form,
      target_ratio: ratioEnabled.value ? form.target_ratio : null,
      kompress_model: kompressDisabled.value ? "disabled" : form.kompress_model,
    });
    ElMessage.success("压缩配置已保存");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <h2>压缩配置</h2>
  <el-card v-loading="loading" style="max-width: 720px">
    <el-form :model="form" label-width="170px">
      <el-form-item label="压缩模式">
        <el-radio-group v-model="form.mode">
          <el-radio-button value="on">开启</el-radio-button>
          <el-radio-button value="tools_only">仅工具输出</el-radio-button>
          <el-radio-button value="off">关闭</el-radio-button>
        </el-radio-group>
      </el-form-item>

      <template v-if="form.mode !== 'off'">
        <el-form-item label="压缩用户消息">
          <el-switch
            v-model="form.compress_user_messages"
            :disabled="form.mode === 'tools_only'"
          />
          <span class="form-hint">仅工具输出模式下强制关闭</span>
        </el-form-item>
        <el-form-item label="压缩系统消息">
          <el-switch
            v-model="form.compress_system_messages"
            :disabled="form.mode === 'tools_only'"
          />
        </el-form-item>
        <el-form-item label="保护最近消息数">
          <el-input-number v-model="form.protect_recent" :min="0" :max="100" />
          <span class="form-hint">最近 N 条消息不压缩,保持对话连贯</span>
        </el-form-item>
        <el-form-item label="目标保留比例">
          <el-switch v-model="ratioEnabled" active-text="启用" />
          <template v-if="ratioEnabled">
            <el-slider
              v-model="form.target_ratio"
              :min="0.1"
              :max="1"
              :step="0.05"
              show-input
              style="width: 320px; margin-left: 12px"
            />
          </template>
          <span v-else class="form-hint">由压缩模型自动决定(约保留 15%)</span>
        </el-form-item>
        <el-form-item label="最小压缩阈值">
          <el-input-number
            v-model="form.min_tokens_to_compress"
            :min="0"
            :step="50"
          />
          <span class="form-hint">短于该 token 数的消息不压缩</span>
        </el-form-item>
        <el-form-item label="Kompress ML 模型">
          <el-switch
            v-model="kompressDisabled"
            active-text="禁用"
            inactive-text="启用"
          />
          <span class="form-hint">
            禁用后仅规则变换(SmartCrusher/CacheAligner),不加载 ML 模型
          </span>
        </el-form-item>
        <el-form-item v-if="!kompressDisabled" label="自定义模型 ID">
          <el-input
            v-model="form.kompress_model"
            placeholder="留空使用默认 chopratejas/kompress-v2-base"
            clearable
          />
        </el-form-item>
      </template>

      <el-alert
        type="info"
        :closable="false"
        title="压缩失败时会自动降级为直通(不压缩直接转发),不影响服务可用性。"
        style="margin-bottom: 16px"
      />

      <el-form-item>
        <el-button type="primary" :loading="saving" @click="save"
          >保存配置</el-button
        >
      </el-form-item>
    </el-form>
  </el-card>
</template>

<style scoped>
.form-hint {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
