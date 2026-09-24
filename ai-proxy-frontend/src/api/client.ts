import axios from "axios";
import { ElMessage } from "element-plus";

// 统一 axios 实例:拦截器集中处理错误提示
const client = axios.create({ baseURL: "/", timeout: 30000 });

client.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const msg =
      error.response?.data?.error?.message ?? error.message ?? "请求失败";
    ElMessage.error(msg);
    return Promise.reject(error);
  },
);

export default client;
