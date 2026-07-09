import { Button, Card, DatePicker, message, Popconfirm, Space, Table } from 'antd';
import { useEffect, useState } from 'react';
import { MetricCards, Status } from '@/components/Common';
import { api } from '@/services/api';
import type { Draft } from '@/types';

function formatTime(value = '') {
  return value.replace('T', ' ').slice(0, 16);
}

function frontendStage(stage = 'title') {
  return stage === 'images' ? 'image' : stage;
}

export default function Drafts() {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [loading, setLoading] = useState(false);
  const [dates, setDates] = useState<[string, string]>(['', '']);

  async function loadDrafts() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (dates[0]) params.set('start_date', dates[0]);
      if (dates[1]) params.set('end_date', dates[1]);
      const data = await api.drafts(params);
      setDrafts(data.drafts || []);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '读取草稿失败');
    } finally {
      setLoading(false);
    }
  }

  async function deleteDraft(id: number) {
    try {
      await api.deleteDraft(id);
      setDrafts((prev) => prev.filter((draft) => draft.id !== id));
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除草稿失败');
    }
  }

  useEffect(() => {
    loadDrafts();
  }, []);

  return (
    <>
      <MetricCards
        items={[
          { label: '全部草稿', value: String(drafts.length), note: '按最近更新时间排序' },
          { label: '标题确认', value: String(drafts.filter((draft) => frontendStage(draft.restore_stage) === 'title').length), note: '可恢复到标题确认' },
          { label: '生成文章', value: String(drafts.filter((draft) => frontendStage(draft.restore_stage) === 'article').length), note: '可继续编辑正文' },
          { label: '生成图片 / 排版', value: String(drafts.filter((draft) => ['image', 'layout'].includes(frontendStage(draft.restore_stage))).length), note: '可继续选图或发布' },
        ]}
      />
      <Card
        className="panel"
        title="内容创作草稿"
        extra={<span className="muted">内容创作任一步骤完成后都会自动保存到草稿箱。</span>}
      >
        <div className="toolbar">
          <Space wrap>
            <DatePicker placeholder="开始日期" onChange={(_, date) => setDates((prev) => [String(date || ''), prev[1]])} />
            <DatePicker placeholder="结束日期" onChange={(_, date) => setDates((prev) => [prev[0], String(date || '')])} />
            <Button onClick={loadDrafts}>查询</Button>
          </Space>
        </div>
        <Table
          loading={loading}
          rowKey="id"
          pagination={false}
          dataSource={drafts}
          columns={[
            { title: '草稿标题', dataIndex: 'title', render: (value) => <strong>{value}</strong> },
            { title: '当前步骤', render: (_, row) => <Status value={row.stage} /> },
            { title: '内容平台', dataIndex: 'platform' },
            { title: '更新时间', render: (_, row) => formatTime(row.updated_at) },
            {
              title: '操作',
              render: (_, row) => (
                <Space>
                  <Button type="link" onClick={() => { window.location.href = `/creator?project_id=${row.project_id}&stage=${row.restore_stage}`; }}>继续创作</Button>
                  <Popconfirm title={`确定删除草稿「${row.title}」吗？`} onConfirm={() => deleteDraft(row.id)}>
                    <Button type="link" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </>
  );
}
