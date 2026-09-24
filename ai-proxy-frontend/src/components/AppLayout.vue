<script setup lang="ts">
// 全局布局:左侧导航 + 右侧内容区
const menus = [
  { path: "/", label: "仪表盘", icon: "Odometer" },
  { path: "/models", label: "模型管理", icon: "Cpu" },
  { path: "/routing", label: "路由策略", icon: "Guide" },
  { path: "/compression", label: "压缩配置", icon: "Files" },
  { path: "/logs", label: "请求日志", icon: "Document" },
  { path: "/settings", label: "系统设置", icon: "Setting" },
];
</script>

<template>
  <el-container class="layout">
    <el-aside width="220px">
      <div class="logo">LLM 代理网关</div>
      <el-menu :default-active="$route.path" router>
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          <el-icon><component :is="m.icon" /></el-icon>
          <span>{{ m.label }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-main>
      <slot />
    </el-main>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100vh;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 600;
  border-bottom: 1px solid var(--el-border-color);
}
.el-aside {
  border-right: 1px solid var(--el-border-color);
}
.el-menu {
  border-right: none;
}
</style>
