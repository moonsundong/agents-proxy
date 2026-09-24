import { defineStore } from "pinia";
import { ref } from "vue";
import { listModels } from "@/api/models";
import type { LLMModel } from "@/types/model";

export const useModelStore = defineStore("models", () => {
  const models = ref<LLMModel[]>([]);
  const loading = ref(false);

  async function fetchModels() {
    loading.value = true;
    try {
      models.value = await listModels();
    } finally {
      loading.value = false;
    }
  }

  return { models, loading, fetchModels };
});
