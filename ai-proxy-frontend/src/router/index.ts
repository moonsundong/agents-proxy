import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      name: "dashboard",
      component: () => import("@/views/Dashboard.vue"),
    },
    {
      path: "/models",
      name: "models",
      component: () => import("@/views/Models.vue"),
    },
    {
      path: "/routing",
      name: "routing",
      component: () => import("@/views/Routing.vue"),
    },
    {
      path: "/compression",
      name: "compression",
      component: () => import("@/views/Compression.vue"),
    },
    {
      path: "/logs",
      name: "logs",
      component: () => import("@/views/Logs.vue"),
    },
    {
      path: "/settings",
      name: "settings",
      component: () => import("@/views/Settings.vue"),
    },
  ],
});

export default router;
