import type { PublishAction } from '@/types';

export interface TaskFormValues {
  platform: string;
  accountId?: number | null;
  accountName: string;
  topic: string;
  articleRequirements?: string;
  taskType: string;
  publishAction?: PublishAction;
  executionMode: 'once' | 'repeat';
  repeatRule?: 'daily' | 'weekly' | '';
  weeklyDay?: number | null;
  executeAt: string;
}

export function buildTaskPayload(values: TaskFormValues) {
  const supportedTask =
    (values.platform === '微信公众号' && values.taskType === '微信公众号图文生成') ||
    (values.platform === '小红书' && values.taskType === '小红书图文生成');
  if (!supportedTask) {
    throw new Error('V1 仅支持微信公众号和小红书图文生成任务');
  }
  return {
    platform: values.platform,
    account_id: values.accountId ?? null,
    account_name: values.accountName,
    topic: values.topic,
    article_requirements: values.articleRequirements || '',
    execute_time: values.executeAt,
    task_type: values.taskType,
    publish_action: values.publishAction || 'publish',
    execution_mode: values.executionMode,
    repeat_rule: values.executionMode === 'repeat' ? values.repeatRule || 'daily' : '',
    weekly_day: values.executionMode === 'repeat' && values.repeatRule === 'weekly' ? values.weeklyDay ?? null : null,
    status: '待执行',
  };
}

export function taskStatusClass(value = '') {
  if (['已授权', '监测中', '成功', '已发布', '已生成平台草稿', '已同步', '启用'].includes(value)) return 'ok';
  if (['待执行', '执行中', '已重试', '同步中'].includes(value)) return 'info';
  if (['未授权', '未监测', '暂停'].includes(value)) return 'warn';
  return 'fail';
}
