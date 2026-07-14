import { Alert, Button, DatePicker, Drawer, Form, Input, message, Modal, Popconfirm, Select, Space, Table, TimePicker } from 'antd';
import dayjs from 'dayjs';
import { useEffect, useMemo, useState } from 'react';
import { Detail, Platform, Status } from '@/components/Common';
import { api } from '@/services/api';
import type { PlatformAccount, PublishJobStatus, ScheduledTask } from '@/types';
import { buildTaskPayload } from '@/utils/taskPayload';

const taskTypeOptions = [
  { value: '微信公众号图文生成', label: '微信公众号图文生成' },
  { value: '小红书图文生成', label: '小红书图文生成（本地内部）' },
  { value: '视频号内容发布', label: '视频号内容发布（本地内部）' },
  { value: '抖音内容发布', label: '抖音内容发布（本地内部）' },
];

const platformOptions = ['', '微信公众号', '视频号', '小红书', '抖音'];

const publishJobStatusLabels: Record<PublishJobStatus, string> = {
  pending: '等待处理',
  checking_login: '检查登录状态',
  uploading_assets: '上传素材',
  filling_content: '填写内容',
  platform_draft: '已生成平台草稿',
  publishing: '发布中',
  awaiting_confirmation: '待人工确认',
  published: '已发布',
  failed: '失败',
};

function publishActionLabel(action?: string) {
  return action === 'draft' ? '同步到平台草稿箱' : '一键发布';
}

function taskInitialValues(task?: ScheduledTask | null) {
  if (!task) {
    return {
      task_type: '微信公众号图文生成',
      publish_action: 'publish',
      execution_mode: 'once',
      repeat_rule: 'daily',
      weekly_day: 1,
    };
  }
  return {
    account_id: task.account_id || undefined,
    topic: task.topic,
    article_requirements: task.article_requirements,
    task_type: task.task_type,
    publish_action: task.publish_action || 'publish',
    execution_mode: task.execution_mode || 'once',
    repeat_rule: task.repeat_rule || 'daily',
    weekly_day: task.weekly_day ?? 1,
    execute_datetime: task.execution_mode === 'repeat' ? undefined : dayjs(task.execute_time.replace(' ', 'T')),
    execute_time_only: task.execution_mode === 'repeat' ? dayjs(task.execute_time, 'HH:mm') : undefined,
  };
}

export default function Tasks() {
  const [form] = Form.useForm();
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [platform, setPlatform] = useState('');
  const [search, setSearch] = useState('');
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailTask, setDetailTask] = useState<ScheduledTask | null>(null);
  const [retryMode, setRetryMode] = useState(false);
  const [selectedRetryIds, setSelectedRetryIds] = useState<number[]>([]);
  const [loadError, setLoadError] = useState('');
  const [accountsLoading, setAccountsLoading] = useState(false);
  const [resumingJobId, setResumingJobId] = useState<number | null>(null);

  async function loadTasks(silent = false) {
    if (!silent) setLoading(true);
    try {
      const params = new URLSearchParams();
      if (status) params.set('status', status);
      if (platform) params.set('platform', platform);
      if (search) params.set('q', search);
      const data = await api.tasks(params);
      setTasks(data.tasks || []);
      setLoadError('');
    } catch (error) {
      const text = error instanceof Error ? error.message : '读取任务失败';
      setLoadError(text);
      message.error(text);
    } finally {
      if (!silent) setLoading(false);
    }
  }

  async function loadAccounts() {
    setAccountsLoading(true);
    try {
      const params = new URLSearchParams({ auth_status: '已授权', platform: '微信公众号' });
      const data = await api.accounts(params);
      const authorizedAccounts = (data.accounts || []).filter((account) => account.platform === '微信公众号' && account.auth_status === '已授权');
      setAccounts(authorizedAccounts);
      return authorizedAccounts;
    } catch (error) {
      message.error(error instanceof Error ? error.message : '读取账号失败');
      return [];
    } finally {
      setAccountsLoading(false);
    }
  }

  useEffect(() => {
    loadAccounts();
  }, []);

  useEffect(() => {
    loadTasks();
    const timer = window.setInterval(() => loadTasks(true), 10_000);
    return () => window.clearInterval(timer);
  }, [status, platform]);

  async function openForm(task?: ScheduledTask) {
    await loadAccounts();
    setEditingTask(task || null);
    form.setFieldsValue(taskInitialValues(task));
    setDrawerOpen(true);
  }

  async function submitTask(values: any) {
    const account = accounts.find((item) => item.id === values.account_id);
    if (!account) {
      message.warning('请先选择已授权账号');
      return;
    }
    const executionMode = values.execution_mode as 'once' | 'repeat';
    const executeAt =
      executionMode === 'repeat'
        ? values.execute_time_only?.format('HH:mm')
        : values.execute_datetime?.format('YYYY-MM-DD HH:mm');
    if (!executeAt) {
      message.warning('请配置执行时间');
      return;
    }
    const payload = buildTaskPayload({
      platform: account.platform,
      accountId: account.id,
      accountName: account.name,
      topic: values.topic,
      articleRequirements: values.article_requirements,
      taskType: values.task_type,
      publishAction: values.publish_action,
      executionMode,
      repeatRule: values.repeat_rule,
      weeklyDay: values.weekly_day,
      executeAt,
    });
    try {
      if (editingTask) await api.updateTask(editingTask.id, payload);
      else await api.createTask(payload);
      setDrawerOpen(false);
      await loadTasks();
      message.success(editingTask ? '任务已更新并进入待执行' : '定时任务已创建并进入待执行');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '保存任务失败');
    }
  }

  async function deleteTask(task: ScheduledTask) {
    try {
      await api.deleteTask(task.id);
      await loadTasks();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除任务失败');
    }
  }

  async function openDetail(id: number) {
    try {
      const task = await api.task(id);
      setDetailTask(task);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '读取详情失败');
    }
  }

  async function retrySelected() {
    try {
      await api.retryTasks(selectedRetryIds);
      setRetryMode(false);
      setSelectedRetryIds([]);
      await loadTasks();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '重试失败');
    }
  }

  async function resumeTaskPublish(task: ScheduledTask) {
    if (!task.publish_job || task.publish_job.status !== 'awaiting_confirmation') return;
    setResumingJobId(task.publish_job.id);
    try {
      await api.resumePublishJob(task.publish_job.id);
      const updated = await api.task(task.id);
      if (detailTask?.id === task.id) setDetailTask(updated);
      await loadTasks(true);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '继续发布失败');
    } finally {
      setResumingJobId(null);
    }
  }

  const executionMode = Form.useWatch('execution_mode', form) || 'once';
  const repeatRule = Form.useWatch('repeat_rule', form) || 'daily';
  const accountOptions = accounts.map((item) => ({
    value: item.id,
    label: `${item.name} / ${item.platform}`,
  }));

  const rowSelection = useMemo(() => retryMode ? {
    selectedRowKeys: selectedRetryIds,
    onChange: (keys: React.Key[]) => setSelectedRetryIds(keys.map(Number)),
    getCheckboxProps: (record: ScheduledTask) => ({ disabled: record.status !== '失败' }),
  } : undefined, [retryMode, selectedRetryIds]);

  return (
    <>
      <div className="panel task-list-panel">
        {loadError && <Alert className="block-gap" type="warning" showIcon message={loadError} />}
        <div className="toolbar">
          <Space wrap>
            <Select value={status} style={{ width: 170 }} onChange={setStatus} options={[
              { value: '', label: '全部状态' },
              { value: '待执行', label: '待执行' },
              { value: '执行中', label: '执行中' },
              { value: '待人工确认', label: '待人工确认' },
              { value: '已生成平台草稿', label: '已生成平台草稿' },
              { value: '已发布', label: '已发布' },
              { value: '失败', label: '失败' },
              { value: '已重试', label: '已重试' },
            ]} />
            <Select value={platform} style={{ width: 150 }} onChange={setPlatform} options={platformOptions.map((value) => ({ value, label: value || '全部平台' }))} />
            <Input.Search placeholder="搜索主题或账号" value={search} onChange={(event) => setSearch(event.target.value)} onSearch={() => loadTasks()} style={{ width: 240 }} />
          </Space>
          <Space>
            {retryMode ? (
              <>
                <Button type="primary" disabled={!selectedRetryIds.length} onClick={retrySelected}>确认重试</Button>
                <Button onClick={() => { setRetryMode(false); setSelectedRetryIds([]); }}>取消</Button>
              </>
            ) : (
              <Button onClick={() => { setStatus('失败'); setRetryMode(true); }}>批量重试</Button>
            )}
            <Button type="primary" onClick={() => openForm()}>新增任务</Button>
          </Space>
        </div>
        {retryMode && <div className="notice">请选择需要立即重新执行的失败任务。未选择的失败任务不会变化。</div>}
        <Table
          loading={loading}
          rowKey="id"
          rowSelection={rowSelection}
          pagination={false}
          dataSource={tasks}
          columns={[
            { title: '平台', render: (_, row) => <Platform value={row.platform} /> },
            { title: '账号', dataIndex: 'account_name' },
            { title: '下次执行时间', render: (_, row) => row.next_run_at || row.execute_time },
            { title: '任务类型', dataIndex: 'task_type' },
            { title: '主题', render: (_, row) => <><strong>{row.selected_title || row.topic}</strong><br /><span className="muted">主题：{row.topic}</span></> },
            { title: '发布方式', render: (_, row) => publishActionLabel(row.publish_action) },
            { title: '状态', render: (_, row) => <Status value={row.status} /> },
            {
              title: '平台结果',
              render: (_, row) => row.publish_job ? (
                <Space direction="vertical" size={0}>
                  <Status value={publishJobStatusLabels[row.publish_job.status]} />
                  {row.publish_job.platform_draft_id && <span className="muted">草稿 ID：{row.publish_job.platform_draft_id}</span>}
                  {row.publish_job.platform_publish_id && <span className="muted">发布 ID：{row.publish_job.platform_publish_id}</span>}
                  {row.publish_job.platform_url && <a href={row.publish_job.platform_url} target="_blank" rel="noreferrer">查看平台结果</a>}
                  {row.publish_job.status === 'failed' && <span className="muted">{row.publish_job.failure_reason || '平台发布失败'}</span>}
                  {row.publish_job.status === 'awaiting_confirmation' && (
                    <Button type="link" loading={resumingJobId === row.publish_job.id} onClick={() => resumeTaskPublish(row)}>继续确认发布</Button>
                  )}
                </Space>
              ) : <span className="muted">尚未执行</span>,
            },
            {
              title: '操作',
              render: (_, row) => (
                <Space>
                  <Button type="link" onClick={() => openDetail(row.id)}>详情</Button>
                  <Button type="link" onClick={() => openForm(row)}>编辑</Button>
                  <Popconfirm title={`确定删除任务「${row.topic}」吗？`} onConfirm={() => deleteTask(row)}>
                    <Button type="link" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </div>
      <Modal centered width={640} title={editingTask ? '编辑任务' : '新增任务'} open={drawerOpen} onCancel={() => setDrawerOpen(false)} footer={null}>
        <Form form={form} layout="vertical" onFinish={submitTask}>
          {!accounts.length && <Alert className="block-gap" showIcon type="warning" message="请先在账号矩阵添加并授权账号，再创建定时任务。" />}
          <Form.Item label="配置账号" name="account_id" rules={[{ required: true, message: '请选择已授权账号' }]}>
            <Select loading={accountsLoading} showSearch optionFilterProp="label" options={accountOptions} placeholder="请选择已授权账号" />
          </Form.Item>
          <Form.Item label="配置主题" name="topic" rules={[{ required: true, message: '请输入任务主题' }]}>
            <Input placeholder="请输入任务主题" />
          </Form.Item>
          <p className="muted">主题只是选题约束，具体标题会在任务执行时结合账号信息和创作要求由 AI 自动选择。</p>
          <Form.Item label="创作要求（非必填）" name="article_requirements">
            <Input.TextArea placeholder="例如：面向小微企业老板，加入老板熬夜对账的例子，突出 AI 记账能把 8 小时压缩到 5 分钟。" />
          </Form.Item>
          <Form.Item label="配置任务类型" name="task_type" rules={[{ required: true }]}>
            <Select options={taskTypeOptions} />
          </Form.Item>
          <Form.Item label="发布方式" name="publish_action" rules={[{ required: true, message: '请选择发布方式' }]}>
            <Select options={[
              { value: 'publish', label: '一键发布' },
              { value: 'draft', label: '同步到平台草稿箱' },
            ]} />
          </Form.Item>
          <Form.Item label="执行方式" name="execution_mode" rules={[{ required: true }]}>
            <Select options={[
              { value: 'once', label: '单次执行' },
              { value: 'repeat', label: '重复执行' },
            ]} />
          </Form.Item>
          {executionMode === 'repeat' && (
            <>
              <Form.Item label="重复规则" name="repeat_rule" rules={[{ required: true }]}>
                <Select options={[
                  { value: 'daily', label: '每天' },
                  { value: 'weekly', label: '每周' },
                ]} />
              </Form.Item>
              {repeatRule === 'weekly' && (
                <Form.Item label="每周星期" name="weekly_day" rules={[{ required: true }]}>
                  <Select options={['周一', '周二', '周三', '周四', '周五', '周六', '周日'].map((label, index) => ({ value: index, label }))} />
                </Form.Item>
              )}
            </>
          )}
          {executionMode === 'repeat' ? (
            <Form.Item label="配置执行时间" name="execute_time_only" rules={[{ required: true, message: '请选择执行时间' }]}>
              <TimePicker format="HH:mm" style={{ width: '100%' }} />
            </Form.Item>
          ) : (
            <Form.Item label="配置执行时间" name="execute_datetime" rules={[{ required: true, message: '请选择执行时间' }]}>
              <DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} />
            </Form.Item>
          )}
          <Space>
            <Button type="primary" htmlType="submit">{editingTask ? '保存修改并进入待执行' : '创建任务并进入待执行'}</Button>
            <Button onClick={() => setDrawerOpen(false)}>取消</Button>
          </Space>
        </Form>
      </Modal>
      <Drawer title={detailTask?.topic} width={620} open={Boolean(detailTask)} onClose={() => setDetailTask(null)}>
        {detailTask && (
          <div className="detail-grid">
            <Detail label="平台" value={detailTask.platform} />
            <Detail label="账号" value={detailTask.account_name} />
            <Detail label="主题约束" value={detailTask.topic} />
            <Detail label="创作要求" value={detailTask.article_requirements || '-'} />
            <Detail label="选定标题" value={detailTask.selected_title || detailTask.topic} />
            <Detail label="任务类型" value={detailTask.task_type} />
            <Detail label="执行时间" value={detailTask.execute_time} />
            <Detail label="执行方式" value={detailTask.execution_mode === 'repeat' ? '重复执行' : '单次执行'} />
            <Detail label="发布方式" value={publishActionLabel(detailTask.publish_action)} />
            <Detail label="状态" value={<Status value={detailTask.status} />} />
            <Detail label="发布结果" value={detailTask.publish_result || '-'} />
            <Detail label="发布任务状态" value={detailTask.publish_job ? <Status value={publishJobStatusLabels[detailTask.publish_job.status]} /> : '-'} />
            <Detail label="平台草稿 ID" value={detailTask.publish_job?.platform_draft_id || '-'} />
            <Detail label="平台发布 ID" value={detailTask.publish_job?.platform_publish_id || '-'} />
            <Detail label="平台结果地址" value={detailTask.publish_job?.platform_url ? <a href={detailTask.publish_job.platform_url} target="_blank" rel="noreferrer">查看平台结果</a> : '-'} />
            <Detail label="失败阶段" value={detailTask.publish_job?.failure_stage || detailTask.failure_stage || '-'} />
            <Detail label="失败原因" value={detailTask.publish_job?.failure_reason || detailTask.failure_reason || '-'} />
            {detailTask.publish_job?.status === 'awaiting_confirmation' && (
              <Detail
                label="人工确认"
                value={(
                  <Space direction="vertical" size={4}>
                    <span>请由运营人员使用微信扫描平台弹出的确认二维码，完成后继续发布。</span>
                    <Button type="primary" loading={resumingJobId === detailTask.publish_job.id} onClick={() => resumeTaskPublish(detailTask)}>继续确认发布</Button>
                  </Space>
                )}
              />
            )}
          </div>
        )}
      </Drawer>
    </>
  );
}
