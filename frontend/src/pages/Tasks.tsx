import { Button, DatePicker, Drawer, Form, Input, message, Modal, Popconfirm, Select, Space, Table, TimePicker } from 'antd';
import dayjs from 'dayjs';
import { useEffect, useMemo, useState } from 'react';
import { Detail, Platform, Status } from '@/components/Common';
import { accounts } from '@/data/mock';
import { api } from '@/services/api';
import type { ScheduledTask } from '@/types';
import { buildTaskPayload } from '@/utils/taskPayload';

const taskTypeOptions = [
  { value: '微信公众号图文生成', label: '微信公众号图文生成' },
  { value: '小红书图文生成（预留）', label: '小红书图文生成（预留）' },
];

function taskInitialValues(task?: ScheduledTask | null) {
  if (!task) {
    return {
      platform: '公众号',
      account_name: accounts[0].name,
      task_type: '微信公众号图文生成',
      execution_mode: 'once',
      repeat_rule: 'daily',
      weekly_day: 1,
    };
  }
  return {
    platform: task.platform,
    account_name: task.account_name,
    topic: task.topic,
    article_requirements: task.article_requirements,
    task_type: task.task_type,
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
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [platform, setPlatform] = useState('');
  const [search, setSearch] = useState('');
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailTask, setDetailTask] = useState<ScheduledTask | null>(null);
  const [retryMode, setRetryMode] = useState(false);
  const [selectedRetryIds, setSelectedRetryIds] = useState<number[]>([]);

  async function loadTasks() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (status) params.set('status', status);
      if (platform) params.set('platform', platform);
      if (search) params.set('q', search);
      const data = await api.tasks(params);
      setTasks(data.tasks || []);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '读取任务失败');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadTasks();
  }, [status, platform]);

  function openForm(task?: ScheduledTask) {
    setEditingTask(task || null);
    form.setFieldsValue(taskInitialValues(task));
    setDrawerOpen(true);
  }

  async function submitTask(values: any) {
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
      platform: values.platform,
      accountName: values.account_name,
      topic: values.topic,
      articleRequirements: values.article_requirements,
      taskType: values.task_type,
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

  const executionMode = Form.useWatch('execution_mode', form) || 'once';
  const repeatRule = Form.useWatch('repeat_rule', form) || 'daily';

  const rowSelection = useMemo(() => retryMode ? {
    selectedRowKeys: selectedRetryIds,
    onChange: (keys: React.Key[]) => setSelectedRetryIds(keys.map(Number)),
    getCheckboxProps: (record: ScheduledTask) => ({ disabled: record.status !== '失败' }),
  } : undefined, [retryMode, selectedRetryIds]);

  return (
    <>
      <div className="panel task-list-panel">
        <div className="toolbar">
          <Space wrap>
            <Select value={status} style={{ width: 150 }} onChange={setStatus} options={[
              { value: '', label: '全部状态' },
              { value: '待执行', label: '待执行' },
              { value: '执行中', label: '执行中' },
              { value: '成功', label: '成功' },
              { value: '失败', label: '失败' },
              { value: '已重试', label: '已重试' },
            ]} />
            <Select value={platform} style={{ width: 150 }} onChange={setPlatform} options={['', '公众号', '小红书', '视频号', '抖音'].map((value) => ({ value, label: value || '全部平台' }))} />
            <Input.Search placeholder="搜索主题或账号" value={search} onChange={(event) => setSearch(event.target.value)} onSearch={loadTasks} style={{ width: 240 }} />
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
            { title: '状态', render: (_, row) => <Status value={row.status} /> },
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
          <Form.Item label="配置平台" name="platform" rules={[{ required: true }]}><Select options={['公众号', '小红书'].map((value) => ({ value }))} /></Form.Item>
          <Form.Item label="配置账号" name="account_name" rules={[{ required: true }]}><Select options={accounts.map((item) => ({ value: item.name }))} /></Form.Item>
          <Form.Item label="配置主题" name="topic" rules={[{ required: true, message: '请输入任务主题' }]}>
            <Input placeholder="请输入任务主题" />
          </Form.Item>
          <p className="muted">主题只是选题约束，具体标题会在任务执行时由 AI 生成建议并自动选择。</p>
          <Form.Item label="创作要求（非必填）" name="article_requirements">
            <Input.TextArea placeholder="例如：面向小微企业老板，加入老板熬夜对账的例子，突出 AI 记账能把 8 小时压缩到 5 分钟。" />
          </Form.Item>
          <Form.Item label="配置任务类型" name="task_type" rules={[{ required: true }]}>
            <Select options={taskTypeOptions} />
          </Form.Item>
          <p className="muted">当前仅微信公众号图文生成会真实执行，小红书为预留能力。</p>
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
            <Detail label="状态" value={<Status value={detailTask.status} />} />
            <Detail label="失败阶段" value={detailTask.failure_stage || '-'} />
            <Detail label="失败原因" value={detailTask.failure_reason || '-'} />
          </div>
        )}
      </Drawer>
    </>
  );
}
