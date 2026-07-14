import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const creator = readFileSync('src/pages/Creator.tsx', 'utf8');
const drafts = readFileSync('src/pages/Drafts.tsx', 'utf8');
const tasks = readFileSync('src/pages/Tasks.tsx', 'utf8');

describe('publishing entry points', () => {
  it.each([['Creator', creator], ['Drafts', drafts]])('%s offers both real publish actions to authorized WeChat accounts', (_, source) => {
    expect(source).toContain('同步到平台草稿箱');
    expect(source).toContain('一键发布');
    expect(source).toContain("auth_status: '已授权'");
    expect(source).toContain("platform: '微信公众号'");
    expect(source).toContain('platform_draft_id');
    expect(source).toContain('platform_publish_id');
    expect(source).toContain('resumePublishJob');
    expect(source).toContain('retryPublishJob');
  });

  it('keeps drafts before layout limited to continue and delete actions', () => {
    expect(drafts).toContain("row.restore_stage === 'layout'");
    expect(drafts).toContain('继续创作');
    expect(drafts).toContain('删除');
  });

  it('hydrates and displays task publish jobs without exposing legacy takeover URLs', () => {
    expect(tasks).toContain("publish_action: task.publish_action || 'publish'");
    expect(tasks).toContain('label="发布方式"');
    expect(tasks).toContain('detailTask.publish_job');
    expect(tasks).toContain('resumePublishJob');
    expect(tasks).not.toContain('manual_takeover_url');
  });
});
