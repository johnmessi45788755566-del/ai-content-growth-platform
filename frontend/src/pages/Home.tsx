import { Button, Card, Space, Table } from 'antd';
import { Bar, MetricCards, Platform, Status } from '@/components/Common';

const taskRows = [
  ['公众号', 'AI记账助手', '2026-07-08 09:30', 'AI 记账如何降低人工成本', '待执行'],
  ['公众号', '小微账房', '2026-07-08 13:00', '老板如何看懂每日流水', '执行中'],
  ['公众号', '老板懂财务', '2026-07-07 18:00', '现金流预警的三个信号', '失败'],
];

export default function Home() {
  return (
    <>
      <MetricCards
        items={[
          { label: '已添加账号', value: '20', note: '公众号 12，小红书 5，视频号 3' },
          { label: '今日任务', value: '18', note: '待执行 11，执行中 3，失败 1' },
          { label: '今日生成内容', value: '36', note: '已发布 29，待发布 6' },
          { label: 'AI 规则更新', value: '7', note: '4 条自动生效，3 条可回滚' },
        ]}
      />
      <div className="grid-2">
        <Card className="panel" title="账号分布" extra={<Button type="link" onClick={() => { window.location.href = '/accounts'; }}>查看账号</Button>}>
          <Bar label="公众号" percent={72} value="12 个" />
          <Bar label="小红书" percent={38} value="5 个" />
          <Bar label="视频号" percent={24} value="3 个" />
          <Bar label="授权失效" percent={16} value="2 个" />
        </Card>
        <Card className="panel" title="数据摘要" extra={<Button type="link" onClick={() => { window.location.href = '/analytics'; }}>查看数据</Button>}>
          <Space direction="vertical" size={12}>
            <div className="topic-direction-card">
              <strong>AI记账助手 推荐数提升 18%</strong>
              <p className="muted">近 7 天案例型标题表现更好，AI 已更新标题偏好。</p>
            </div>
            <div className="topic-direction-card">
              <strong>小微账房 收藏数提升 12%</strong>
              <p className="muted">清单型正文更容易被收藏，建议延续该结构。</p>
            </div>
          </Space>
        </Card>
      </div>
      <Card className="panel block-gap" title="今日任务" extra={<Button type="primary" onClick={() => { window.location.href = '/tasks'; }}>进入任务中心</Button>}>
        <Table
          rowKey={(row) => row.join('-')}
          pagination={false}
          dataSource={taskRows}
          columns={[
            { title: '平台', render: (_, row) => <Platform value={row[0]} /> },
            { title: '账号', render: (_, row) => row[1] },
            { title: '执行时间', render: (_, row) => row[2] },
            { title: '主题', render: (_, row) => row[3] },
            { title: '状态', render: (_, row) => <Status value={row[4]} /> },
          ]}
        />
      </Card>
    </>
  );
}
