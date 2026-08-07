import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'Chat', component: () => import('../App.vue') },
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
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
