import {
  BarChartOutlined,
  CalendarOutlined,
  EditOutlined,
  HomeOutlined,
  SettingOutlined,
  TableOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { Layout, Menu } from 'antd';
import type { MenuProps } from 'antd';
import { Outlet } from 'umi';

const { Sider, Header, Content } = Layout;

const pageTitles: Record<string, string> = {
  '/home': '首页',
  '/accounts': '账号矩阵',
  '/analytics': '数据分析',
  '/creator': '内容创作',
  '/drafts': '草稿箱',
  '/tasks': '任务中心',
  '/settings': '系统设置',
};

const items: MenuProps['items'] = [
  { key: '/home', icon: <HomeOutlined />, label: '首页' },
  { key: '/accounts', icon: <TableOutlined />, label: '账号矩阵' },
  { key: '/analytics', icon: <BarChartOutlined />, label: '数据分析' },
  { key: '/creator', icon: <EditOutlined />, label: '内容创作' },
  { key: '/drafts', icon: <UnorderedListOutlined />, label: '草稿箱' },
  { key: '/tasks', icon: <CalendarOutlined />, label: '任务中心' },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
];

export default function AppLayout() {
  const selectedKey = window.location.pathname || '/home';

  return (
    <Layout className="app-shell">
      <Sider width={232} className="sidebar" theme="light">
        <div className="brand">
          <div className="brand-mark">矩</div>
          <div>
            <strong>矩阵工作台</strong>
            <span>AI 内容运营</span>
          </div>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={items}
          onClick={({ key }) => {
            window.location.href = String(key);
          }}
          className="main-menu"
        />
        <div className="sidebar-footer">
          <span>当前企业</span>
          <strong>好财 AI 运营组</strong>
        </div>
      </Sider>
      <Layout className="main">
        <Header className="topbar">
          <div>
            <p className="eyebrow">新媒体矩阵运营工作台</p>
            <h1>{pageTitles[selectedKey] || '首页'}</h1>
          </div>
        </Header>
        <Content className="view-root">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
