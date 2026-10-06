// 路由：/login 独立页，其余套 layout；守卫无 token 重定向 /login
import { createRouter, createWebHistory } from 'vue-router';

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/login/index.vue') },
  { path: '/change-password', name: 'change-password', component: () => import('../views/change-password/index.vue') },
  {
    path: '/',
    component: () => import('../layout/index.vue'),
    redirect: '/dashboard',
    children: [
      {path:'operations',component:()=>import('../views/operations/index.vue'),meta:{title:'巡查计划与运行情况'}},
      { path: 'dashboard', name: 'workbench', component: () => import('../views/workbench/index.vue'), meta: { title: '工作台' } },
      { path: 'members', name: 'members', component: () => import('../views/members/index.vue'), meta: { title: '组织与项目' } },
      { path: 'knowledge', name: 'knowledge', component: () => import('../views/knowledge/index.vue'), meta: { title: '知识库管理' } },
      { path: 'supervise', name: 'supervise', component: () => import('../views/supervise/index.vue'), meta: { title: '工单监督' } },
      { path: 'notifications', name: 'notifications', component: () => import('../views/notifications/index.vue'), meta: { title: '通知记录' } },
      { path: 'sla', name: 'sla', component: () => import('../views/sla/index.vue'), meta: { title: 'SLA 时限配置' } },
      { path: 'reports', name: 'reports', component: () => import('../views/reports/index.vue'), meta: { title: '报表与导出' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  const token = localStorage.getItem('token');
  const mustChange = localStorage.getItem('mustChangePassword') === '1';
  if (to.path !== '/login' && !token) {
    return { path: '/login', query: { redirect: to.fullPath } };
  }
  if (to.path === '/login' && token) {
    return mustChange ? '/change-password' : '/dashboard';
  }
  // 强制改密：未改密前只能停留在改密页
  if (token && mustChange && to.path !== '/change-password') {
    return '/change-password';
  }
  // SLA 配置页：仅 sla:write 权限可见可入；报表导出页：仅 export 权限可见可入
  // （权限点由 layout 挂载时 /api/auth/me 刷新）
  if (to.path === '/sla' || to.path === '/reports') {
    let perms = [];
    try {
      perms = (JSON.parse(localStorage.getItem('userInfo') || 'null') || {}).perms || [];
    } catch (e) {
      perms = [];
    }
    const required = to.path === '/sla' ? 'sla:write' : 'export';
    if (!perms.includes(required)) {
      return '/supervise';
    }
  }
  return true;
});

export default router;
