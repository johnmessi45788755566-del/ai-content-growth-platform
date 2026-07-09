export interface TaskFormValues {
  platform: string;
  accountName: string;
  topic: string;
  articleRequirements?: string;
  taskType: string;
  executionMode: 'once' | 'repeat';
  repeatRule?: 'daily' | 'weekly' | '';
  weeklyDay?: number | null;
  executeAt: string;
}

export function buildTaskPayload(values: TaskFormValues) {
  return {
    platform: values.platform,
    account_name: values.accountName,
    topic: values.topic,
    article_requirements: values.articleRequirements || '',
    execute_time: values.executeAt,
    task_type: values.taskType,
    execution_mode: values.executionMode,
    repeat_rule: values.executionMode === 'repeat' ? values.repeatRule || 'daily' : '',
    weekly_day: values.executionMode === 'repeat' && values.repeatRule === 'weekly' ? values.weeklyDay ?? null : null,
    status: '待执行',
  };
}

export function taskStatusClass(value = '') {
  if (['已授权', '监测中', '成功', '已发布', '启用'].includes(value)) return 'ok';
  if (['待执行', '执行中', '已重试'].includes(value)) return 'info';
  if (['未授权', '未监测', '暂停'].includes(value)) return 'warn';
  return 'fail';
}
