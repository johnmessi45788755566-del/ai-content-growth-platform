import { Alert, Button, Card, DatePicker, Form, message, Modal, Popconfirm, Select, Space, Table } from 'antd';
import { useEffect, useState } from 'react';
import { MetricCards } from '@/components/Common';
import { api } from '@/services/api';
import type { Draft, PlatformAccount, PublishAction, PublishJob } from '@/types';

function formatTime(value = '') {
  return value.replace('T', ' ').slice(0, 16);
}

function frontendStage(stage = 'title') {
  return stage === 'images' ? 'image' : stage;
}

const publishTerminalStatuses = ['platform_draft', 'published', 'awaiting_confirmation', 'failed'];

async function waitForPublishJob(initialJob: PublishJob) {
  let job = initialJob;
  while (!publishTerminalStatuses.includes(job.status)) {
    await new Promise((resolve) => window.setTimeout(resolve, 1500));
    job = await api.getPublishJob(job.id);
  }
  return job;
}

export default function Drafts() {
  const [publishForm] = Form.useForm();
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [loading, setLoading] = useState(false);
  const [dates, setDates] = useState<[string, string]>(['', '']);
  const [loadError, setLoadError] = useState('');
  const [publishDraft, setPublishDraft] = useState<Draft | null>(null);
  const [publishAccounts, setPublishAccounts] = useState<PlatformAccount[]>([]);
  const [publishAccountsLoading, setPublishAccountsLoading] = useState(false);
  const [publishJob, setPublishJob] = useState<PublishJob | null>(null);
  const [publishingAction, setPublishingAction] = useState<PublishAction | ''>('');

  async function loadDrafts() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (dates[0]) params.set('start_date', dates[0]);
      if (dates[1]) params.set('end_date', dates[1]);
      const data = await api.drafts(params);
      setDrafts(data.drafts || []);
      setLoadError('');
    } catch (error) {
      const text = error instanceof Error ? error.message : '读取草稿失败';
      setLoadError(text);
      message.error(text);
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

  async function loadPublishAccounts() {
    setPublishAccountsLoading(true);
    try {
      const data = await api.accounts(new URLSearchParams({ auth_status: '已授权', platform: '微信公众号' }));
      const accounts = (data.accounts || []).filter((account) => account.platform === '微信公众号' && account.auth_status === '已授权');
      setPublishAccounts(accounts);
      if (accounts.length) publishForm.setFieldValue('account_id', accounts[0].id);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载授权账号失败');
    } finally {
      setPublishAccountsLoading(false);
    }
  }

  async function openPublishModal(draft: Draft) {
    setPublishDraft(draft);
    setPublishJob(null);
    publishForm.resetFields();
    await loadPublishAccounts();
  }

  async function runPublishAction(action: PublishAction) {
    if (!publishDraft) return;
    const values = await publishForm.validateFields() as { account_id: number };
    const account = publishAccounts.find((item) => item.id === values.account_id);
    if (!account) {
      message.error('所选账号不存在或授权状态已变化，请刷新账号');
      return;
    }
    setPublishingAction(action);
    try {
      const created = await api.createPublishJob({
        project_id: publishDraft.project_id,
        account_id: account.id,
        action,
      });
      setPublishJob(created);
      setPublishJob(await waitForPublishJob(created));
    } catch (error) {
      message.error(error instanceof Error ? error.message : '发布失败');
    } finally {
      setPublishingAction('');
    }
  }

  async function retryPublishJob() {
    if (!publishJob) return;
    setPublishingAction(publishJob.action);
    try {
      const job = await api.retryPublishJob(publishJob.id);
      setPublishJob(job);
      setPublishJob(await waitForPublishJob(job));
    } catch (error) {
      message.error(error instanceof Error ? error.message : '重试失败');
    } finally {
      setPublishingAction('');
    }
  }

  async function resumePublishJob() {
    if (!publishJob) return;
    setPublishingAction('publish');
    try {
      const job = await api.resumePublishJob(publishJob.id);
      setPublishJob(job);
      setPublishJob(await waitForPublishJob(job));
    } catch (error) {
      message.error(error instanceof Error ? error.message : '继续发布失败');
    } finally {
      setPublishingAction('');
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
        {loadError && <Alert className="block-gap" type="warning" showIcon message={loadError} />}
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
            { title: '当前步骤', render: (_, row) => <span className="status draft-stage-status">{row.stage}</span> },
            { title: '内容平台', dataIndex: 'platform' },
            { title: '更新时间', render: (_, row) => formatTime(row.updated_at) },
            {
              title: '操作',
              render: (_, row) => (
                <Space>
                  <Button type="link" onClick={() => { window.location.href = `/creator?project_id=${row.project_id}&stage=${row.restore_stage}`; }}>继续创作</Button>
                  {row.restore_stage === 'layout' && <Button type="link" onClick={() => openPublishModal(row)}>发布</Button>}
                  <Popconfirm title={`确定删除草稿「${row.title}」吗？`} onConfirm={() => deleteDraft(row.id)}>
                    <Button type="link" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Modal title={`发布配置${publishDraft ? ` · ${publishDraft.title}` : ''}`} open={Boolean(publishDraft)} onCancel={() => setPublishDraft(null)} footer={null}>
        <Form form={publishForm} layout="vertical">
          <Form.Item label="选择账号" name="account_id" rules={[{ required: true, message: '请选择已授权公众号账号' }]}>
            <Select
              disabled={Boolean(publishJob)}
              loading={publishAccountsLoading}
              placeholder={publishAccounts.length ? '请选择已授权公众号账号' : '暂无已授权公众号账号，请先到账号矩阵登录'}
              options={publishAccounts.map((account) => ({ value: account.id, label: account.name }))}
            />
          </Form.Item>
          {publishJob && (
            <Alert
              className="block-gap"
              showIcon
              type={publishJob.status === 'failed' ? 'error' : publishJob.status === 'awaiting_confirmation' ? 'warning' : ['platform_draft', 'published'].includes(publishJob.status) ? 'success' : 'info'}
              message={publishJob.status === 'platform_draft' ? '平台草稿已创建' : publishJob.status === 'published' ? '平台发布已完成' : publishJob.status === 'awaiting_confirmation' ? '等待运营人员确认' : publishJob.status === 'failed' ? '平台发布失败' : '平台正在处理'}
              description={(
                <Space direction="vertical" size={4}>
                  {publishJob.status === 'platform_draft' && <span>平台草稿 ID：{publishJob.platform_draft_id || '-'}</span>}
                  {publishJob.status === 'published' && <span>平台发布 ID：{publishJob.platform_publish_id || '-'}</span>}
                  {publishJob.platform_url && <a href={publishJob.platform_url} target="_blank" rel="noreferrer">查看平台结果</a>}
                  {publishJob.status === 'awaiting_confirmation' && <span>请由运营人员使用微信扫描平台弹出的确认二维码，完成后点击继续确认发布。</span>}
                  {publishJob.status === 'awaiting_confirmation' && <Button type="primary" loading={publishingAction === 'publish'} onClick={resumePublishJob}>继续确认发布</Button>}
                  {publishJob.status === 'failed' && <span>失败阶段：{publishJob.failure_stage || '-'}<br />失败原因：{publishJob.failure_reason || '-'}</span>}
                  {publishJob.status === 'failed' && <Button loading={Boolean(publishingAction)} onClick={retryPublishJob}>重试</Button>}
                </Space>
              )}
            />
          )}
          <Space wrap>
            <Button
              loading={publishingAction === 'draft'}
              disabled={!publishAccounts.length || Boolean(publishingAction) || Boolean(publishJob && !['platform_draft', 'published'].includes(publishJob.status))}
              onClick={() => runPublishAction('draft')}
            >
              同步到平台草稿箱
            </Button>
            <Button
              type="primary"
              loading={publishingAction === 'publish'}
              disabled={!publishAccounts.length || Boolean(publishingAction) || Boolean(publishJob && !['platform_draft', 'published'].includes(publishJob.status))}
              onClick={() => runPublishAction('publish')}
            >
              一键发布
            </Button>
            <Button disabled={Boolean(publishJob)} loading={publishAccountsLoading} onClick={loadPublishAccounts}>刷新账号</Button>
            <Button onClick={() => setPublishDraft(null)}>取消</Button>
          </Space>
        </Form>
      </Modal>
    </>
  );
}
