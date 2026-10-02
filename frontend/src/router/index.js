import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/login', name: 'Login', component: () => import('../views/Login.vue') },
  { path: '/', name: 'Chat', component: () => import('../components/chat/ChatView.vue') },
  { path: '/dashboard', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/schema', name: 'Schema', component: () => import('../views/Schema.vue') },
  { path: '/settings', name: 'Settings', component: () => import('../views/Settings.vue') },
  { path: '/admin', name: 'Admin', component: () => import('../views/admin/AdminLayout.vue') },
  { path: '/admin/meta', name: 'MetaConfig', component: () => import('../views/admin/MetaConfig.vue') },
  { path: '/admin/glossary', name: 'Glossary', component: () => import('../views/admin/Glossary.vue') },
  { path: '/admin/datasource', name: 'DataSource', component: () => import('../views/admin/DataSource.vue') },
  { path: '/admin/prompts', name: 'Prompts', component: () => import('../views/admin/Prompts.vue') },
  { path: '/admin/feedback', name: 'Feedback', component: () => import('../views/admin/Feedback.vue') },
  { path: '/admin/audit', name: 'Audit', component: () => import('../views/admin/Audit.vue') },
  { path: '/admin/pipelines', name: 'Pipelines', component: () => import('../views/admin/PipelinesDashboard.vue') },
  { path: '/admin/readiness', name: 'Readiness', component: () => import('../views/admin/ReadinessDashboard.vue') },
  { path: '/admin/gov', name: 'Gov', component: () => import('../views/admin/GovDashboard.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Auth guard: no token -> login page (token may be expired; api.js
// also redirects to /login on any 401). e2e tests inject a token via
// addInitScript so they pass through.
router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (to.path !== '/login' && !token) {
    return { path: '/login', query: to.fullPath && to.fullPath !== '/' ? { redirect: to.fullPath } : {} }
  }
  if (to.path === '/login' && token) return '/'
})

export default router
