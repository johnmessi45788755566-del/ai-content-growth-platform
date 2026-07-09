import { Button, Card, Table } from 'antd';
import { Detail, Platform, Status } from '@/components/Common';

export default function Settings() {
  return (
    <div className="grid-2">
      <Card className="panel" title="平台授权配置" extra={<Button>去授权</Button>}>
        <Table
          rowKey={(row) => row[0]}
          pagination={false}
          dataSource={[
            ['公众号', '已授权', '12 个', '10 分钟前'],
            ['小红书', '未授权', '0 个', '未检测'],
            ['视频号', '授权失效', '1 个', '30 分钟前'],
          ]}
          columns={[
            { title: '平台', render: (_, row) => <Platform value={row[0]} /> },
            { title: '授权状态', render: (_, row) => <Status value={row[1]} /> },
            { title: '授权账号数', render: (_, row) => row[2] },
            { title: '最近检测', render: (_, row) => row[3] },
            { title: '操作', render: () => <Button type="link">检测</Button> },
          ]}
        />
      </Card>
      <Card className="panel" title="AI 配置">
        <div className="detail-grid">
          <Detail label="默认模型" value="内容生成模型 V1" />
          <Detail label="默认风格" value="专业、科普、案例化" />
          <Detail label="敏感词" value="节税承诺、夸大收益、未经确认政策" />
          <Detail label="规则生效" value="自动生效，保留回滚记录" />
        </div>
      </Card>
    </div>
  );
}
