// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import Creator from '../src/pages/Creator';
import Drafts from '../src/pages/Drafts';
import Tasks from '../src/pages/Tasks';

const api = vi.hoisted(() => ({
  accounts: vi.fn(),
  createProject: vi.fn(),
  createPublishJob: vi.fn(),
  createTask: vi.fn(),
  deleteDraft: vi.fn(),
  deleteTask: vi.fn(),
  drafts: vi.fn(),
  generateArticle: vi.fn(),
  generateImages: vi.fn(),
  getProject: vi.fn(),
  getPublishJob: vi.fn(),
  resumePublishJob: vi.fn(),
  retryPublishJob: vi.fn(),
  retryTasks: vi.fn(),
  saveArticle: vi.fn(),
  saveLayout: vi.fn(),
  selectMedia: vi.fn(),
  task: vi.fn(),
  tasks: vi.fn(),
  topicRecommendations: vi.fn(),
  updateStage: vi.fn(),
  updateTask: vi.fn(),
}));

vi.mock('@/services/api', () => ({
  api,
  assetUrl: (url = '') => url,
}));

const authorizedAccount = {
  id: 11,
  platform: '微信公众号',
  name: '授权公众号',
  auth_status: '已授权',
};

const unavailableAccounts = [
  { id: 12, platform: '微信公众号', name: '未授权公众号', auth_status: '未授权' },
  { id: 13, platform: '小红书', name: '其他平台账号', auth_status: '已授权' },
];

function project(id: number, title: string) {
  return {
    id,
    title,
    platform: '微信公众号',
    current_stage: 'layout' as const,
    article_blocks: [{ block_type: 'intro', content: `${title}正文` }],
    media_assets: [],
    layout_blocks: [],
  };
}

function publishJob(overrides: Record<string, unknown> = {}) {
  return {
    id: 81,
    project_id: 1,
    account_id: authorizedAccount.id,
    platform: '微信公众号',
    action: 'draft',
    status: 'platform_draft',
    platform_draft_id: 'draft-81',
    platform_url: 'https://mp.weixin.qq.com/draft-81',
    requires_confirmation: false,
    ...overrides,
  };
}

function scheduledTask(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    platform: '微信公众号',
    account_id: authorizedAccount.id,
    account_name: authorizedAccount.name,
    execute_time: '2026-07-14 18:00',
    task_type: '微信公众号图文生成',
    topic: `任务 ${id}`,
    status: '待人工确认',
    publish_action: 'publish',
    execution_mode: 'once',
    ...overrides,
  };
}

function buttonsWithText(text: string) {
  const expected = text.replace(/\s/g, '');
  return Array.from(document.querySelectorAll('button'))
    .filter((button) => button.textContent?.replace(/\s/g, '') === expected);
}

function clickButton(text: string) {
  const buttons = buttonsWithText(text);
  expect(buttons).toHaveLength(1);
  fireEvent.click(buttons[0]);
}

function clickStep(text: string) {
  const step = screen.getByText(text, { exact: true }).closest('[role="button"]');
  expect(step).not.toBeNull();
  fireEvent.click(step as HTMLElement);
}

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  const getComputedStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = ((element: Element) => getComputedStyle(element)) as typeof window.getComputedStyle;
  window.scrollTo = () => {};
});

beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState({}, '', '/');
  api.accounts.mockResolvedValue({ accounts: [authorizedAccount, ...unavailableAccounts] });
  api.deleteDraft.mockResolvedValue({});
  api.deleteTask.mockResolvedValue({});
  api.retryTasks.mockResolvedValue({});
});

afterEach(() => {
  cleanup();
});

describe('Creator publishing', () => {
  it('maps the two publish buttons to draft and publish for an authorized WeChat account only', async () => {
    const firstProject = project(1, '项目一');
    window.history.replaceState({}, '', '/creator?project_id=1&stage=layout');
    api.getProject.mockResolvedValue(firstProject);
    api.saveLayout.mockResolvedValue({ project: firstProject, layout_blocks: [] });
    api.createPublishJob
      .mockResolvedValueOnce(publishJob())
      .mockResolvedValueOnce(publishJob({ id: 82, action: 'publish', status: 'published', platform_draft_id: '', platform_publish_id: 'publish-82' }));

    render(<Creator />);

    await waitFor(() => expect(api.getProject).toHaveBeenCalledWith(1));
    clickButton('发布');
    await waitFor(() => expect(api.accounts).toHaveBeenCalled());

    const accountSelect = screen.getByRole('combobox');
    fireEvent.mouseDown(accountSelect);
    expect(screen.queryAllByText(authorizedAccount.name).length).toBeGreaterThan(0);
    expect(screen.queryByText(unavailableAccounts[0].name)).toBeNull();
    expect(screen.queryByText(unavailableAccounts[1].name)).toBeNull();

    clickButton('同步到平台草稿箱');
    await waitFor(() => expect(api.createPublishJob).toHaveBeenNthCalledWith(1, {
      project_id: 1,
      account_id: authorizedAccount.id,
      action: 'draft',
    }));

    clickButton('一键发布');
    await waitFor(() => expect(api.createPublishJob).toHaveBeenNthCalledWith(2, {
      project_id: 1,
      account_id: authorizedAccount.id,
      action: 'publish',
    }));
    await waitFor(() => expect(document.body.textContent).toContain('平台发布 ID：publish-82'));
  });

  it('does not render or use a publish job after the active project changes', async () => {
    const firstProject = project(1, '项目一');
    const secondProject = project(2, '项目二');
    window.history.replaceState({}, '', '/creator?project_id=1&stage=layout');
    api.getProject.mockResolvedValue(firstProject);
    api.saveLayout.mockImplementation(async (id) => ({ project: id === 1 ? firstProject : secondProject, layout_blocks: [] }));
    api.updateStage.mockResolvedValue({ project: secondProject, article_blocks: secondProject.article_blocks, media_assets: [], layout_blocks: [] });
    api.createPublishJob.mockResolvedValue(publishJob());

    render(<Creator />);

    await waitFor(() => expect(api.getProject).toHaveBeenCalledWith(1));
    clickButton('发布');
    await waitFor(() => expect(api.accounts).toHaveBeenCalled());
    clickButton('同步到平台草稿箱');
    await waitFor(() => expect(screen.queryByText('平台草稿已创建')).not.toBeNull());
    clickButton('取消');

    clickStep('标题确认');
    await waitFor(() => expect(api.updateStage).toHaveBeenCalledWith(1, 'title'));
    clickStep('文章排版');
    await waitFor(() => expect(api.updateStage).toHaveBeenCalledWith(2, 'layout'));
    clickButton('发布');
    await waitFor(() => expect(api.saveLayout).toHaveBeenLastCalledWith(2, expect.any(Array)));

    expect(screen.queryByText('平台草稿已创建')).toBeNull();
    expect(screen.queryByText('平台草稿 ID：draft-81')).toBeNull();
    expect((screen.getByRole('combobox') as HTMLInputElement).disabled).toBe(false);
  });
});

describe('Draft publishing', () => {
  it('only offers publishing after layout and supports failed retry through confirmation resume', async () => {
    api.drafts.mockResolvedValue({
      drafts: [
        { id: 1, project_id: 1, title: '正文草稿', stage: '生成文章', restore_stage: 'article', platform: '微信公众号', updated_at: '2026-07-14' },
        { id: 2, project_id: 2, title: '排版草稿', stage: '文章排版', restore_stage: 'layout', platform: '微信公众号', updated_at: '2026-07-14' },
      ],
    });
    api.createPublishJob.mockResolvedValue(publishJob({ project_id: 2, action: 'publish', status: 'failed', platform_draft_id: '', platform_url: '', failure_stage: '发布文章', failure_reason: '平台拒绝' }));
    api.retryPublishJob.mockResolvedValue(publishJob({ project_id: 2, action: 'publish', status: 'awaiting_confirmation', platform_draft_id: '', platform_url: '', requires_confirmation: true }));
    api.resumePublishJob.mockResolvedValue(publishJob({ project_id: 2, action: 'publish', status: 'published', platform_draft_id: '', platform_publish_id: 'publish-88' }));

    render(<Drafts />);

    await waitFor(() => expect(screen.queryByText('排版草稿')).not.toBeNull());
    expect(buttonsWithText('发布')).toHaveLength(1);
    clickButton('发布');
    await waitFor(() => expect(api.accounts).toHaveBeenCalled());
    clickButton('一键发布');
    await waitFor(() => expect(api.createPublishJob).toHaveBeenCalledWith({
      project_id: 2,
      account_id: authorizedAccount.id,
      action: 'publish',
    }));
    await waitFor(() => expect(document.body.textContent).toContain('失败阶段：发布文章'));

    clickButton('重试');
    await waitFor(() => expect(api.retryPublishJob).toHaveBeenCalledWith(81));
    expect(document.body.textContent).toContain('扫描平台弹出的确认二维码');

    clickButton('继续确认发布');
    await waitFor(() => expect(api.resumePublishJob).toHaveBeenCalledWith(81));
    await waitFor(() => expect(document.body.textContent).toContain('平台发布 ID：publish-88'));
  });
});

describe('Task publishing results', () => {
  it('renders hydrated publish jobs and refreshes the task row after resume', async () => {
    const published = scheduledTask(1, {
      topic: '已发布任务',
      status: '已发布',
      publish_job: publishJob({ id: 91, action: 'publish', status: 'published', platform_draft_id: '', platform_publish_id: 'publish-91' }),
    });
    const waiting = scheduledTask(2, {
      topic: '待确认任务',
      publish_job: publishJob({ id: 92, project_id: 2, action: 'publish', status: 'awaiting_confirmation', platform_draft_id: '', platform_url: '', requires_confirmation: true }),
    });
    const resumedJob = publishJob({ id: 92, project_id: 2, action: 'publish', status: 'published', platform_draft_id: '', platform_publish_id: 'publish-92' });
    const resumed = scheduledTask(2, {
      topic: '待确认任务',
      status: '已发布',
      publish_job: resumedJob,
    });
    api.tasks.mockResolvedValueOnce({ tasks: [published, waiting] }).mockResolvedValue({ tasks: [published, resumed] });
    api.task.mockResolvedValue(resumed);
    api.resumePublishJob.mockResolvedValue(resumedJob);

    render(<Tasks />);

    await waitFor(() => expect(screen.queryByText('发布 ID：publish-91')).not.toBeNull());
    expect(screen.queryAllByText('待人工确认').length).toBeGreaterThan(0);
    clickButton('继续确认发布');

    await waitFor(() => expect(api.resumePublishJob).toHaveBeenCalledWith(92));
    expect(api.task).toHaveBeenCalledWith(2);
    await waitFor(() => expect(api.tasks.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(screen.queryByText('发布 ID：publish-92')).not.toBeNull();
  });
});
