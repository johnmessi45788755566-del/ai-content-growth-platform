export default {
  antd: {},
  npmClient: 'pnpm',
  routes: [
    { path: '/', redirect: '/home' },
    { path: '/', component: '@/layouts/AppLayout', routes: [
      { path: '/home', component: '@/pages/Home' },
      { path: '/accounts', component: '@/pages/Accounts' },
      { path: '/analytics', component: '@/pages/Analytics' },
      { path: '/creator', component: '@/pages/Creator' },
      { path: '/drafts', component: '@/pages/Drafts' },
      { path: '/tasks', component: '@/pages/Tasks' },
      { path: '/settings', component: '@/pages/Settings' }
    ] }
  ],
  title: '新媒体矩阵运营工作台',
};
