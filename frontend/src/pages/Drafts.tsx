import { Alert, Button, Card, DatePicker, Form, message, Modal, Popconfirm, Select, Space, Table } from 'antd';
import { useEffect, useRef, useState } from 'react';
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
const maxPublishPollFailures = 2;

async function waitForPublishJob(
  initialJob: PublishJob,
  isCurrent: () => boolean,
  onJob: (job: PublishJob) => void,
) {
  let job = initialJob;
  let consecutiveFailures = 0;
  while (!publishTerminalStatuses.includes(job.status)) {
    await new Promise((resolve) => window.setTimeout(resolve, 1500));
    if (!isCurrent()) return null;
    try {
      const nextJob = await api.getPublishJob(job.id);
      if (!isCurrent()) return null;
      if (nextJob.project_id !== initialJob.project_id) throw new Error('发布任务归属与当前草稿不一致');
      job = nextJob;
      consecutiveFailures = 0;
      onJob(job);
    } catch (error) {
      consecutiveFailures += 1;
      if (consecutiveFailures >= maxPublishPollFailures) throw error;
    }
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
  const [publishPollingError, setPublishPollingError] = useState('');
  const [refreshingPublishJob, setRefreshingPublishJob] = useState(false);
  const publishDraftRef = useRef<Draft | null>(null);
  const publishAccountsProjectIdRef = useRef<number | null>(null);
  const publishRequestGenerationRef = useRef(0);
  const currentPublishJob = publishJob?.project_id === publishDraft?.project_id ? publishJob : null;

  function invalidatePublishRequest() {
    publishRequestGenerationRef.current += 1;
    setPublishingAction('');
    setRefreshingPublishJob(false);
  }

  function beginPublishRequest(targetProjectId: number) {
    const generation = publishRequestGenerationRef.current + 1;
    publishRequestGenerationRef.current = generation;
    setPublishingAction('');
    setRefreshingPublishJob(false);
    setPublishPollingError('');
    return { projectId: targetProjectId, generation };
  }

  function isPublishRequestCurrent(request: { projectId: number; generation: number }) {
    return publishDraftRef.current?.project_id === request.projectId
      && publishRequestGenerationRef.current === request.generation;
  }

  function acceptPublishJob(job: PublishJob, request: { projectId: number; generation: number }) {
    if (!isPublishRequestCurrent(request)) return false;
    if (job.project_id !== request.projectId) throw new Error('发布任务归属与当前草稿不一致');
    setPublishJob(job);
    return true;
  }

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

  function commitPublishAccounts(
    targetProjectId: number,
    accounts: PlatformAccount[],
    preferredAccountId?: number,
    fallbackToFirst = false,
  ) {
    const currentId = publishForm.getFieldValue('account_id');
    let nextAccountId = accounts.some((account) => account.id === currentId)
      ? currentId
      : accounts[0]?.id;
    if (preferredAccountId !== undefined) {
      nextAccountId = accounts.some((account) => account.id === preferredAccountId)
        ? preferredAccountId
        : undefined;
    } else if (fallbackToFirst) {
      nextAccountId = accounts[0]?.id;
    }
    publishAccountsProjectIdRef.current = targetProjectId;
    setPublishAccounts(accounts);
    publishForm.setFieldValue('account_id', nextAccountId);
  }

  async function loadPublishAccounts() {
    const data = await api.accounts(new URLSearchParams({ auth_status: '已授权', platform: '微信公众号' }));
    return (data.accounts || []).filter((account) => account.platform === '微信公众号' && account.auth_status === '已授权');
  }

  async function restorePublishJob(targetProjectId: number) {
    const request = beginPublishRequest(targetProjectId);
    try {
      const resource = await api.projectPublishJob(targetProjectId);
      if (!isPublishRequestCurrent(request)) return;
      setPublishAccountsLoading(true);
      const accounts = await loadPublishAccounts();
      if (!isPublishRequestCurrent(request)) return;
      commitPublishAccounts(
        targetProjectId,
        accounts,
        resource.job?.account_id,
        resource.job === null,
      );
      setPublishAccountsLoading(false);
      const existing = resource.job;
      if (!existing) {
        setPublishJob(null);
        return;
      }
      if (!acceptPublishJob(existing, request)) return;
      await waitForPublishJob(
        existing,
        () => isPublishRequestCurrent(request),
        (job) => acceptPublishJob(job, request),
      );
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      const text = error instanceof Error ? error.message : '恢复发布状态失败';
      setPublishPollingError(text);
      message.error(text);
    } finally {
      if (isPublishRequestCurrent(request)) setPublishAccountsLoading(false);
    }
  }

  async function refreshPublishAccounts() {
    const targetProjectId = publishDraftRef.current?.project_id;
    if (!targetProjectId) return;
    const request = beginPublishRequest(targetProjectId);
    setPublishAccountsLoading(true);
    try {
      const accounts = await loadPublishAccounts();
      if (!isPublishRequestCurrent(request)) return;
      commitPublishAccounts(targetProjectId, accounts);
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      message.error(error instanceof Error ? error.message : '加载授权账号失败');
    } finally {
      if (isPublishRequestCurrent(request)) setPublishAccountsLoading(false);
    }
  }

  async function openPublishModal(draft: Draft) {
    invalidatePublishRequest();
    publishDraftRef.current = draft;
    publishAccountsProjectIdRef.current = null;
    setPublishDraft(draft);
    setPublishAccounts([]);
    setPublishAccountsLoading(false);
    setPublishJob(null);
    setPublishPollingError('');
    publishForm.resetFields();
    await restorePublishJob(draft.project_id);
  }

  function closePublishModal() {
    invalidatePublishRequest();
    publishDraftRef.current = null;
    publishAccountsProjectIdRef.current = null;
    setPublishDraft(null);
    setPublishAccounts([]);
    setPublishAccountsLoading(false);
    setPublishJob(null);
    setPublishPollingError('');
    publishForm.resetFields();
  }

  async function runPublishAction(action: PublishAction) {
    if (!publishDraft) return;
    const targetProjectId = publishDraft.project_id;
    const request = beginPublishRequest(targetProjectId);
    const values = await publishForm.validateFields() as { account_id: number };
    if (!isPublishRequestCurrent(request)) return;
    if (publishAccountsProjectIdRef.current !== targetProjectId) {
      message.error('所选账号不属于当前草稿，请刷新账号');
      return;
    }
    const account = publishAccounts.find((item) => item.id === values.account_id);
    if (!account) {
      message.error('所选账号不存在或授权状态已变化，请刷新账号');
      return;
    }
    setPublishingAction(action);
    let acceptedJob = false;
    try {
      const created = await api.createPublishJob({
        project_id: targetProjectId,
        account_id: account.id,
        action,
      });
      acceptedJob = acceptPublishJob(created, request);
      if (!acceptedJob) return;
      await waitForPublishJob(
        created,
        () => isPublishRequestCurrent(request),
        (job) => acceptPublishJob(job, request),
      );
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      const text = error instanceof Error ? error.message : '发布失败';
      if (acceptedJob) setPublishPollingError(text);
      message.error(text);
    } finally {
      if (isPublishRequestCurrent(request)) setPublishingAction('');
    }
  }

  async function retryPublishJob() {
    if (!currentPublishJob) return;
    const request = beginPublishRequest(currentPublishJob.project_id);
    setPublishingAction(currentPublishJob.action);
    try {
      const job = await api.retryPublishJob(currentPublishJob.id);
      if (!acceptPublishJob(job, request)) return;
      await waitForPublishJob(
        job,
        () => isPublishRequestCurrent(request),
        (nextJob) => acceptPublishJob(nextJob, request),
      );
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      const text = error instanceof Error ? error.message : '重试失败';
      setPublishPollingError(text);
      message.error(text);
    } finally {
      if (isPublishRequestCurrent(request)) setPublishingAction('');
    }
  }

  async function resumePublishJob() {
    if (!currentPublishJob) return;
    const request = beginPublishRequest(currentPublishJob.project_id);
    setPublishingAction('publish');
    try {
      const job = await api.resumePublishJob(currentPublishJob.id);
      if (!acceptPublishJob(job, request)) return;
      await waitForPublishJob(
        job,
        () => isPublishRequestCurrent(request),
        (nextJob) => acceptPublishJob(nextJob, request),
      );
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      const text = error instanceof Error ? error.message : '继续发布失败';
      setPublishPollingError(text);
      message.error(text);
    } finally {
      if (isPublishRequestCurrent(request)) setPublishingAction('');
    }
  }

  async function refreshPublishJob() {
    if (!currentPublishJob) return;
    const request = beginPublishRequest(currentPublishJob.project_id);
    setRefreshingPublishJob(true);
    try {
      const job = await api.getPublishJob(currentPublishJob.id);
      if (!acceptPublishJob(job, request)) return;
      await waitForPublishJob(
        job,
        () => isPublishRequestCurrent(request),
        (nextJob) => acceptPublishJob(nextJob, request),
      );
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      const text = error instanceof Error ? error.message : '刷新发布状态失败';
      setPublishPollingError(text);
      message.error(text);
    } finally {
      if (isPublishRequestCurrent(request)) setRefreshingPublishJob(false);
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
      <Modal title={`发布配置${publishDraft ? ` · ${publishDraft.title}` : ''}`} open={Boolean(publishDraft)} onCancel={closePublishModal} footer={null}>
        <Form form={publishForm} layout="vertical">
          <Form.Item label="选择账号" name="account_id" rules={[{ required: true, message: '请选择已授权公众号账号' }]}>
            <Select
              disabled={Boolean(currentPublishJob)}
              loading={publishAccountsLoading}
              placeholder={publishAccounts.length ? '请选择已授权公众号账号' : '暂无已授权公众号账号，请先到账号矩阵登录'}
              options={publishAccounts.map((account) => ({ value: account.id, label: account.name }))}
            />
          </Form.Item>
          {currentPublishJob && (
            <Alert
              className="block-gap"
              showIcon
              type={currentPublishJob.status === 'failed' ? 'error' : currentPublishJob.status === 'awaiting_confirmation' ? 'warning' : ['platform_draft', 'published'].includes(currentPublishJob.status) ? 'success' : 'info'}
              message={currentPublishJob.status === 'platform_draft' ? '平台草稿已创建' : currentPublishJob.status === 'published' ? '平台发布已完成' : currentPublishJob.status === 'awaiting_confirmation' ? '等待运营人员确认' : currentPublishJob.status === 'failed' ? '平台发布失败' : '平台正在处理'}
              description={(
                <Space direction="vertical" size={4}>
                  <span>发布任务 ID：{currentPublishJob.id}</span>
                  {currentPublishJob.status === 'platform_draft' && <span>平台草稿 ID：{currentPublishJob.platform_draft_id || '-'}</span>}
                  {currentPublishJob.status === 'published' && <span>平台发布 ID：{currentPublishJob.platform_publish_id || '-'}</span>}
                  {currentPublishJob.platform_url && <a href={currentPublishJob.platform_url} target="_blank" rel="noreferrer">查看平台结果</a>}
                  {currentPublishJob.status === 'awaiting_confirmation' && <span>请由运营人员使用微信扫描平台弹出的确认二维码，完成后点击继续确认发布。</span>}
                  {currentPublishJob.status === 'awaiting_confirmation' && <Button type="primary" loading={publishingAction === 'publish'} onClick={resumePublishJob}>继续确认发布</Button>}
                  {currentPublishJob.status === 'failed' && <span>失败阶段：{currentPublishJob.failure_stage || '-'}<br />失败原因：{currentPublishJob.failure_reason || '-'}</span>}
                  {currentPublishJob.status === 'failed' && <Button loading={Boolean(publishingAction)} onClick={retryPublishJob}>重试</Button>}
                </Space>
              )}
            />
          )}
          {currentPublishJob && publishPollingError && (
            <Alert
              className="block-gap"
              showIcon
              type="warning"
              message="发布状态刷新失败"
              description={(
                <Space direction="vertical" size={4}>
                  <span>{publishPollingError}</span>
                  <Button loading={refreshingPublishJob} onClick={refreshPublishJob}>刷新发布状态</Button>
                </Space>
              )}
            />
          )}
          <Space wrap>
            <Button
              loading={publishingAction === 'draft'}
              disabled={!publishAccounts.length || Boolean(publishingAction) || Boolean(currentPublishJob && !['platform_draft', 'published'].includes(currentPublishJob.status))}
              onClick={() => runPublishAction('draft')}
            >
              同步到平台草稿箱
            </Button>
            <Button
              type="primary"
              loading={publishingAction === 'publish'}
              disabled={!publishAccounts.length || Boolean(publishingAction) || Boolean(currentPublishJob && !['platform_draft', 'published'].includes(currentPublishJob.status))}
              onClick={() => runPublishAction('publish')}
            >
              一键发布
            </Button>
            <Button disabled={Boolean(currentPublishJob)} loading={publishAccountsLoading} onClick={refreshPublishAccounts}>刷新账号</Button>
            <Button onClick={closePublishModal}>取消</Button>
          </Space>
        </Form>
      </Modal>
    </>
  );
}
