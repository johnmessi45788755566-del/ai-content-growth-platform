import { describe, expect, it } from 'vitest';
import { buildTaskPayload } from '../src/utils/taskPayload';

describe('buildTaskPayload', () => {
  it('keeps one-off task execution as full datetime', () => {
    const payload = buildTaskPayload({
      platform: '微信公众号',
      accountId: 1,
      accountName: 'AI记账助手',
      topic: 'AI记账',
      articleRequirements: '',
      taskType: '微信公众号图文生成',
      executionMode: 'once',
      executeAt: '2026-07-09 10:48',
    });

    expect(payload.execute_time).toBe('2026-07-09 10:48');
    expect(payload.execution_mode).toBe('once');
    expect(payload.publish_action).toBe('publish');
  });

  it('keeps repeat task execution as time only', () => {
    const payload = buildTaskPayload({
      platform: '微信公众号',
      accountId: 1,
      accountName: 'AI记账助手',
      topic: 'AI记账',
      articleRequirements: '面向小微企业老板',
      taskType: '微信公众号图文生成',
      publishAction: 'draft',
      executionMode: 'repeat',
      repeatRule: 'weekly',
      weeklyDay: 2,
      executeAt: '10:48',
    });

    expect(payload.execute_time).toBe('10:48');
    expect(payload.repeat_rule).toBe('weekly');
    expect(payload.weekly_day).toBe(2);
    expect(payload.article_requirements).toBe('面向小微企业老板');
    expect(payload.publish_action).toBe('draft');
  });

  it('allows Xiaohongshu note tasks', () => {
    const payload = buildTaskPayload({
      platform: '小红书',
      accountId: 2,
      accountName: '小红书账号',
      topic: '小红书图文',
      taskType: '小红书图文生成',
      executionMode: 'once',
      executeAt: '2026-07-09 10:48',
    });

    expect(payload.platform).toBe('小红书');
    expect(payload.task_type).toBe('小红书图文生成');
    expect(payload.publish_action).toBe('publish');
  });

  it('rejects mismatched platform task types', () => {
    expect(() => buildTaskPayload({
      platform: '微信公众号',
      accountId: 1,
      accountName: '授权公众号',
      topic: '预留任务',
      taskType: '小红书图文生成',
      executionMode: 'once',
      executeAt: '2026-07-09 10:48',
    })).toThrow('V1 仅支持微信公众号和小红书图文生成任务');
  });
});
