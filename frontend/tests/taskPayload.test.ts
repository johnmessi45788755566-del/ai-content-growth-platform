import { describe, expect, it } from 'vitest';
import { buildTaskPayload } from '../src/utils/taskPayload';

describe('buildTaskPayload', () => {
  it('keeps one-off task execution as full datetime', () => {
    const payload = buildTaskPayload({
      platform: '公众号',
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
      platform: '公众号',
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
});
