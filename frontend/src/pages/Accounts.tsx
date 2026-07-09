import { Button, Card, Drawer, Input, Select, Space, Table } from 'antd';
import { useState } from 'react';
import { Detail, MetricCards, Platform, SmallTag, Status } from '@/components/Common';
import { accounts } from '@/data/mock';

export default function Accounts() {
  const [active, setActive] = useState<(typeof accounts)[number] | null>(null);
  return (
    <>
      <MetricCards
        items={[
          { label: '已添加', value: '20', note: '上限 1000 个账号' },
          { label: '已授权', value: '16', note: '2 个授权即将到期' },
          { label: '监测中', value: '14', note: '覆盖主要运营账号' },
          { label: '授权失效', value: '2', note: '需运营人处理' },
        ]}
      />
      <Card className="panel">
        <div className="toolbar">
          <Space wrap>
            <Input placeholder="搜索账号名称 / ID" style={{ width: 220 }} />
            <Select defaultValue="全部平台" options={['全部平台', '公众号', '小红书'].map((value) => ({ value }))} />
            <Select defaultValue="全部授权状态" options={['全部授权状态', '已授权', '授权失效'].map((value) => ({ value }))} />
          </Space>
          <Space>
            <Button>授权检测</Button>
            <Button>更新账号信息</Button>
            <Button type="primary">添加账号</Button>
          </Space>
        </div>
        <Table
          rowKey="name"
          pagination={false}
          dataSource={accounts}
          columns={[
            { title: '账号', render: (_, row) => <><strong>{row.name}</strong><br /><span className="muted">{row.role}</span></> },
            { title: '平台', render: (_, row) => <Platform value={row.platform} /> },
            { title: '标签', render: (_, row) => <SmallTag value={row.tag} /> },
            { title: '运营人', dataIndex: 'owner' },
            { title: '授权状态', render: (_, row) => <Status value={row.auth} /> },
            { title: '监测状态', render: (_, row) => <Status value={row.monitor} /> },
            { title: '添加时间', render: () => '2026-03-26 10:54' },
            { title: '操作', render: (_, row) => <Button type="link" onClick={() => setActive(row)}>详情</Button> },
          ]}
        />
      </Card>
      <Drawer title={active?.name} open={Boolean(active)} onClose={() => setActive(null)} width={560}>
        {active && (
          <div className="detail-grid">
            <Detail label="平台" value={active.platform} />
            <Detail label="账号名称" value={active.name} />
            <Detail label="运营人" value={active.owner} />
            <Detail label="账号标签" value={active.tag} />
            <Detail label="授权状态" value={active.auth} />
            <Detail label="监测状态" value={active.monitor} />
            <Detail label="角色定位" value={active.role} />
            <Detail label="面向用户" value={active.audience} />
            <Detail label="内容风格" value={active.style} />
            <Detail label="禁忌方向" value={active.taboo} />
            <Detail label="AI 动态偏好" value={active.preference} />
          </div>
        )}
      </Drawer>
    </>
  );
}
