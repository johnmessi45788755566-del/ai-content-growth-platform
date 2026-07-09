import { Card, Input, Select, Space, Tabs, Table } from 'antd';
import { MetricCards, Platform, Status } from '@/components/Common';
import { accounts, works } from '@/data/mock';

export default function Analytics() {
  return (
    <>
      <div className="toolbar">
        <Tabs
          defaultActiveKey="account"
          items={[
            { key: 'account', label: '账号分析' },
            { key: 'work', label: '作品分析' },
            { key: 'rule', label: 'AI 规则变更' },
          ]}
        />
        <Space wrap>
          <Select defaultValue="近 7 天" options={['近 7 天', '近 30 天'].map((value) => ({ value }))} />
          <Select defaultValue="全部平台" options={['全部平台', '公众号', '小红书'].map((value) => ({ value }))} />
          <Input placeholder="搜索账号或作品" />
        </Space>
      </div>
      <MetricCards
        items={[
          { label: '总推荐数', value: '676,400', note: '较上周期 +14.6%' },
          { label: '总阅读 / 播放', value: '299,300', note: '较上周期 +9.2%' },
          { label: '总互动', value: '25,814', note: '点赞、评论、转发、收藏' },
          { label: '规则变更', value: '7', note: '可查看依据并回滚' },
        ]}
      />
      <Card className="panel">
        <Table
          rowKey="name"
          pagination={false}
          dataSource={accounts}
          columns={[
            { title: '账号', dataIndex: 'name' },
            { title: '平台', render: (_, row) => <Platform value={row.platform} /> },
            { title: '粉丝数', dataIndex: 'fans' },
            { title: '阅读 / 播放', dataIndex: 'reads' },
            { title: '推荐数', dataIndex: 'recommends' },
            { title: '点赞', dataIndex: 'likes' },
            { title: '评论', dataIndex: 'comments' },
            { title: '转发', dataIndex: 'shares' },
            { title: '收藏', dataIndex: 'favorites' },
            { title: '更新时间', render: () => '10 分钟前' },
          ]}
        />
      </Card>
      <Card className="panel block-gap" title="作品分析">
        <Table
          rowKey={(row) => row.join('-')}
          pagination={false}
          dataSource={works}
          columns={[
            { title: '作品标题', render: (_, row) => row[2] },
            { title: '平台', render: (_, row) => <Platform value={row[1]} /> },
            { title: '账号', render: (_, row) => row[0] },
            { title: '类型', render: (_, row) => row[3] },
            { title: '阅读 / 播放', render: (_, row) => row[4] },
            { title: '推荐数', render: (_, row) => row[5] },
            { title: '状态', render: (_, row) => <Status value={row[10]} /> },
          ]}
        />
      </Card>
    </>
  );
}
