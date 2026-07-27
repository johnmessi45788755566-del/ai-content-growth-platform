// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import Creator from '../src/pages/Creator';
import Drafts from '../src/pages/Drafts';
import Tasks from '../src/pages/Tasks';
import type { PublishJob, ScheduledTask } from '../src/types';

const api = vi.hoisted(() => ({
  accounts: vi.fn(),
  createProject: vi.fn(),
  createPublishJob: vi.fn(),
  currentPublishJob: vi.fn(),
  createTask: vi.fn(),
  deleteDraft: vi.fn(),
  deleteTask: vi.fn(),
  drafts: vi.fn(),
  generateArticle: vi.fn(),
  generateImages: vi.fn(),
  getProject: vi.fn(),
  getPublishJob: vi.fn(),
  projectPublishJob: vi.fn(),
  publishMonitoring: vi.fn(),
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
  login_status: '在线',
};

const unavailableAccounts = [
  { id: 12, platform: '微信公众号', name: '未授权公众号', auth_status: '未授权', login_status: '在线' },
  { id: 13, platform: '小红书', name: '其他平台账号', auth_status: '已授权', login_status: '在线' },
];

const boundAccount = {
  ...authorizedAccount,
  id: 14,
  name: '项目绑定公众号',
};

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

function publishJob(overrides: Partial<PublishJob> = {}): PublishJob {
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

function scheduledTask(id: number, overrides: Partial<ScheduledTask> = {}): ScheduledTask {
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
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

async function selectFormOption(labelText: string, optionText: string) {
  const label = screen.getAllByText(labelText, { exact: true }).find((node) => node.tagName.toLowerCase() === 'label');
  expect(label).toBeDefined();
  const formItem = label?.closest('.ant-form-item');
  expect(formItem).not.toBeNull();
  const selector = formItem?.querySelector('.ant-select-selector');
  expect(selector).not.toBeNull();
  fireEvent.mouseDown(selector as HTMLElement);
  const option = await screen.findByText(optionText, { selector: '.ant-select-item-option-content' });
  fireEvent.click(option);
}

function expectButtonReady(text: string) {
  const buttons = buttonsWithText(text);
  expect(buttons).toHaveLength(1);
  expect(buttons[0].disabled).toBe(false);
  expect(buttons[0].classList.contains('ant-btn-loading')).toBe(false);
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
  api.currentPublishJob.mockResolvedValue({ job: null });
  api.projectPublishJob.mockResolvedValue({ job: null });
  api.publishMonitoring.mockResolvedValue({
    platform: '小红书',
    total_jobs: 0,
    published_jobs: 0,
    failed_jobs: 0,
    in_progress_jobs: 0,
    success_rate: 0,
    avg_duration_seconds: 0,
    recent_failures: [],
  });
  api.retryTasks.mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Creator publishing', () => {
  it('seeds the markdown layout editor with generated images from the image step', async () => {
    const restoredProject = {
      ...project(1, '图片传递标题'),
      article_blocks: [
        { block_type: 'intro', label: '引言', content: '引言内容' },
        { block_type: 'body', label: '正文', content: '正文一' },
        { block_type: 'body', label: '正文', content: '正文二' },
      ],
      media_assets: [
        { id: 5, asset_type: 'cover', url: '/cover.svg', selected: 1 },
        { id: 6, asset_type: 'inline', url: '/inline.svg', selected: 1 },
      ],
      layout_blocks: [],
    };
    window.history.replaceState({}, '', '/creator?project_id=1&stage=layout');
    api.getProject.mockResolvedValue(restoredProject);

    render(<Creator />);

    const editor = await screen.findByLabelText('Markdown 排版内容') as HTMLTextAreaElement;
    expect(editor.value).toContain('# 图片传递标题');
    expect(editor.value).toContain('![封面图](asset:5)');
    expect(editor.value).toContain('> 引言内容');
    expect(editor.value).toContain('正文一');
    expect(editor.value).toContain('![正文配图 1](asset:6)');
  });

  it('previews multiple markdown images on the same line', async () => {
    const restoredProject = {
      ...project(1, '一行多图标题'),
      media_assets: [
        { id: 6, asset_type: 'inline', url: '/inline-1.svg', selected: 1 },
        { id: 7, asset_type: 'inline', url: '/inline-2.svg', selected: 1 },
        { id: 8, asset_type: 'inline', url: '/inline-3.svg', selected: 1 },
      ],
      layout_blocks: [
        {
          id: 1,
          project_id: 1,
          section_type: 'markdown',
          content: '# 一行多图标题\n\n![图1](asset:6) ![图2](asset:7) ![图3](asset:8)',
          media_asset_id: null,
          sort_order: 1,
        },
      ],
    };
    window.history.replaceState({}, '', '/creator?project_id=1&stage=layout');
    api.getProject.mockResolvedValue(restoredProject);

    render(<Creator />);

    await screen.findByLabelText('Markdown 排版内容');
    expect(screen.getByAltText('图1').getAttribute('src')).toBe('/inline-1.svg');
    expect(screen.getByAltText('图2').getAttribute('src')).toBe('/inline-2.svg');
    expect(screen.getByAltText('图3').getAttribute('src')).toBe('/inline-3.svg');
  });

  it('loads an existing awaiting-confirmation publish job without second-scan resume', async () => {
    const restoredProject = project(1, '恢复发布项目');
    const waiting = publishJob({
      id: 80,
      project_id: 1,
      account_id: boundAccount.id,
      action: 'publish',
      status: 'awaiting_confirmation',
      platform_draft_id: '',
      requires_confirmation: true,
    });
    window.history.replaceState({}, '', '/creator?project_id=1&stage=layout');
    api.getProject.mockResolvedValue(restoredProject);
    api.saveLayout.mockResolvedValue({ project: restoredProject, layout_blocks: [] });
    api.accounts.mockResolvedValue({ accounts: [authorizedAccount, boundAccount, ...unavailableAccounts] });
    api.projectPublishJob.mockResolvedValue({ job: waiting });
    render(<Creator />);

    await waitFor(() => expect(api.getProject).toHaveBeenCalledWith(1));
    clickButton('发布');
    await screen.findByText('平台要求二次安全确认');
    expect(api.projectPublishJob).toHaveBeenCalledWith(1);
    expect(screen.getAllByText(boundAccount.name).length).toBeGreaterThan(0);
    expect(api.currentPublishJob).not.toHaveBeenCalled();
    expect(api.createPublishJob).not.toHaveBeenCalled();
    expect(api.resumePublishJob).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('当前版本不会发起二次扫码');
    expect(screen.queryByText('继续确认发布')).toBeNull();
  });

  it('keeps an existing job when account restore fails and refreshes accounts independently', async () => {
    const restoredProject = project(1, '账号失败恢复项目');
    const waiting = publishJob({
      id: 85,
      project_id: 1,
      account_id: boundAccount.id,
      action: 'publish',
      status: 'awaiting_confirmation',
      platform_draft_id: '',
      requires_confirmation: true,
    });
    window.history.replaceState({}, '', '/creator?project_id=1&stage=layout');
    api.getProject.mockResolvedValue(restoredProject);
    api.saveLayout.mockResolvedValue({ project: restoredProject, layout_blocks: [] });
    api.projectPublishJob.mockResolvedValue({ job: waiting });
    api.accounts
      .mockRejectedValueOnce(new Error('账号恢复失败'))
      .mockResolvedValueOnce({ accounts: [authorizedAccount, boundAccount] });

    render(<Creator />);

    await waitFor(() => expect(api.getProject).toHaveBeenCalledWith(1));
    clickButton('发布');
    await screen.findByText('平台要求二次安全确认');
    expect(document.body.textContent).toContain('发布任务 ID：85');
    expect(screen.queryByText('发布状态刷新失败')).toBeNull();

    clickButton('刷新账号');
    await waitFor(() => expect(api.accounts).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByText(boundAccount.name).length).toBeGreaterThan(0));
    expect(document.body.textContent).toContain('发布任务 ID：85');
    expect(screen.queryByText('继续确认发布')).toBeNull();
  });

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

    const accountSelect = document.querySelector('#account_id') as HTMLElement;
    expect(accountSelect).not.toBeNull();
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

  it('does not let account refresh cancel a deferred publish response', async () => {
    const restoredProject = project(1, '发布刷新竞态项目');
    const pendingPublish = deferred<PublishJob>();
    window.history.replaceState({}, '', '/creator?project_id=1&stage=layout');
    api.getProject.mockResolvedValue(restoredProject);
    api.saveLayout.mockResolvedValue({ project: restoredProject, layout_blocks: [] });
    api.createPublishJob.mockImplementation(() => pendingPublish.promise);

    render(<Creator />);

    await waitFor(() => expect(api.getProject).toHaveBeenCalledWith(1));
    clickButton('发布');
    await waitFor(() => expect(api.accounts).toHaveBeenCalledTimes(1));
    clickButton('一键发布');
    await waitFor(() => expect(api.createPublishJob).toHaveBeenCalledTimes(1));

    const refreshAccounts = buttonsWithText('刷新账号')[0];
    expect(refreshAccounts.disabled).toBe(true);
    fireEvent.click(refreshAccounts);
    expect(api.accounts).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingPublish.resolve(publishJob({
        id: 86,
        project_id: 1,
        action: 'publish',
        status: 'published',
        platform_draft_id: '',
        platform_publish_id: 'publish-86',
      }));
      await pendingPublish.promise;
    });

    await waitFor(() => expect(document.body.textContent).toContain('平台发布 ID：publish-86'));
  });

  it('ignores a deferred publish response after the active project changes', async () => {
    const firstProject = project(1, '项目一');
    const secondProject = project(2, '项目二');
    const firstPublish = deferred<ReturnType<typeof publishJob>>();
    window.history.replaceState({}, '', '/creator?project_id=1&stage=layout');
    api.getProject.mockResolvedValue(firstProject);
    api.saveLayout.mockImplementation(async (id) => ({ project: id === 1 ? firstProject : secondProject, layout_blocks: [] }));
    api.updateStage.mockResolvedValue({ project: secondProject, article_blocks: secondProject.article_blocks, media_assets: [], layout_blocks: [] });
    api.createPublishJob
      .mockImplementationOnce(() => firstPublish.promise)
      .mockResolvedValueOnce(publishJob({ id: 82, project_id: 2, status: 'published', platform_draft_id: '', platform_publish_id: 'publish-82' }));

    render(<Creator />);

    await waitFor(() => expect(api.getProject).toHaveBeenCalledWith(1));
    clickButton('发布');
    await waitFor(() => expect(api.accounts).toHaveBeenCalled());
    clickButton('同步到平台草稿箱');
    await waitFor(() => expect(api.createPublishJob).toHaveBeenCalledTimes(1));
    clickButton('取消');

    clickStep('标题确认');
    await waitFor(() => expect(api.updateStage).toHaveBeenCalledWith(1, 'title'));
    clickStep('文章排版');
    await waitFor(() => expect(api.updateStage).toHaveBeenCalledWith(2, 'layout'));
    clickButton('发布');
    await waitFor(() => expect(api.saveLayout).toHaveBeenLastCalledWith(2, expect.any(Array)));
    clickButton('一键发布');
    await waitFor(() => expect(document.body.textContent).toContain('平台发布 ID：publish-82'));

    firstPublish.reject(new Error('项目一迟到失败'));
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    expect(screen.queryByText('平台草稿已创建')).toBeNull();
    expect(screen.queryByText('平台草稿 ID：draft-81')).toBeNull();
    expect(screen.queryByText('项目一迟到失败')).toBeNull();
    expect(document.body.textContent).toContain('平台发布 ID：publish-82');
  });

  it('keeps deferred account and restored-job responses owned by the active project', async () => {
    const firstProject = project(1, '账号竞态项目 A');
    const secondProject = project(2, '账号竞态项目 B');
    const firstAccounts = deferred<{ accounts: Array<typeof boundAccount> }>();
    window.history.replaceState({}, '', '/creator?project_id=1&stage=layout');
    api.getProject.mockResolvedValue(firstProject);
    api.saveLayout.mockImplementation(async (id) => ({ project: id === 1 ? firstProject : secondProject, layout_blocks: [] }));
    api.updateStage.mockResolvedValue({ project: secondProject, article_blocks: secondProject.article_blocks, media_assets: [], layout_blocks: [] });
    api.projectPublishJob.mockImplementation(async (projectId) => ({
      job: projectId === 1
        ? publishJob({ id: 91, project_id: 1, account_id: boundAccount.id })
        : null,
    }));
    api.accounts
      .mockImplementationOnce(() => firstAccounts.promise)
      .mockResolvedValueOnce({ accounts: [authorizedAccount] });
    api.createPublishJob.mockResolvedValue(publishJob({
      id: 92,
      project_id: 2,
      account_id: authorizedAccount.id,
      action: 'publish',
      status: 'published',
      platform_draft_id: '',
      platform_publish_id: 'publish-project-B',
    }));

    render(<Creator />);

    await waitFor(() => expect(api.getProject).toHaveBeenCalledWith(1));
    clickButton('发布');
    await waitFor(() => expect(api.accounts).toHaveBeenCalledTimes(1));
    clickButton('取消');

    clickStep('标题确认');
    await waitFor(() => expect(api.updateStage).toHaveBeenCalledWith(1, 'title'));
    clickStep('文章排版');
    await waitFor(() => expect(api.updateStage).toHaveBeenCalledWith(2, 'layout'));
    clickButton('发布');
    await waitFor(() => expect(screen.queryAllByText(authorizedAccount.name).length).toBeGreaterThan(0));

    await act(async () => {
      firstAccounts.resolve({ accounts: [boundAccount] });
      await firstAccounts.promise;
    });

    expect(screen.queryByText('发布任务 ID：91')).toBeNull();
    clickButton('一键发布');
    await waitFor(() => expect(api.createPublishJob).toHaveBeenCalledWith({
      project_id: 2,
      account_id: authorizedAccount.id,
      action: 'publish',
    }));
    await screen.findByText('平台发布已完成');
  });

  it('releases refresh loading when a newer retry fails before the old refresh finishes', async () => {
    const firstProject = project(1, '竞态项目');
    const staleRefresh = deferred<PublishJob>();
    window.history.replaceState({}, '', '/creator?project_id=1&stage=layout');
    api.getProject.mockResolvedValue(firstProject);
    api.saveLayout.mockResolvedValue({ project: firstProject, layout_blocks: [] });
    api.createPublishJob.mockResolvedValue(publishJob({ status: 'failed', failure_reason: '初次发布失败' }));
    api.retryPublishJob
      .mockRejectedValueOnce(new Error('首次重试失败'))
      .mockRejectedValueOnce(new Error('新代次重试失败'));
    api.getPublishJob.mockImplementation(() => staleRefresh.promise);

    render(<Creator />);

    await waitFor(() => expect(api.getProject).toHaveBeenCalledWith(1));
    clickButton('发布');
    await waitFor(() => expect(api.accounts).toHaveBeenCalled());
    clickButton('一键发布');
    await waitFor(() => expect(document.body.textContent).toContain('初次发布失败'));
    clickButton('重试');
    await waitFor(() => expect(screen.queryByText('首次重试失败')).not.toBeNull());

    clickButton('刷新发布状态');
    await waitFor(() => expect(api.getPublishJob).toHaveBeenCalledWith(81));
    clickButton('重试');
    await waitFor(() => expect(screen.queryByText('新代次重试失败')).not.toBeNull());
    staleRefresh.resolve(publishJob({ status: 'published', platform_draft_id: '', platform_publish_id: 'stale-result' }));

    await waitFor(() => expectButtonReady('刷新发布状态'));
    expect(screen.queryByText('平台发布 ID：stale-result')).toBeNull();
  });
});

describe('Draft publishing', () => {
  it('loads an existing failed job for a restored draft and retries that resource', async () => {
    const restoredDraft = { id: 10, project_id: 1, title: '跨刷新草稿', stage: '排版已完成，待发布', restore_stage: 'layout', platform: '微信公众号', updated_at: '2026-07-15 09:00' };
    const failed = publishJob({
      id: 79,
      project_id: 1,
      account_id: boundAccount.id,
      action: 'draft',
      status: 'failed',
      platform_draft_id: '',
      failure_stage: '提交平台草稿',
      failure_reason: '平台暂时不可用',
    });
    api.drafts.mockResolvedValue({ drafts: [restoredDraft] });
    api.accounts.mockResolvedValue({ accounts: [authorizedAccount, boundAccount, ...unavailableAccounts] });
    api.projectPublishJob.mockResolvedValue({ job: failed });
    api.retryPublishJob.mockResolvedValue(publishJob({ id: 79, project_id: 1 }));

    render(<Drafts />);

    await screen.findByText(restoredDraft.title);
    clickButton('发布');
    await screen.findByText('平台发布失败');
    expect(api.projectPublishJob).toHaveBeenCalledWith(1);
    expect(screen.getAllByText(boundAccount.name).length).toBeGreaterThan(0);
    expect(api.currentPublishJob).not.toHaveBeenCalled();

    clickButton('重试');
    await waitFor(() => expect(api.retryPublishJob).toHaveBeenCalledWith(79));
    expect(api.createPublishJob).not.toHaveBeenCalled();
    await screen.findByText('平台草稿已创建');
  });

  it('keeps an existing job when account restore fails and refreshes accounts independently', async () => {
    const restoredDraft = { id: 11, project_id: 1, title: '账号失败恢复草稿', stage: '排版已完成，待发布', restore_stage: 'layout', platform: '微信公众号', updated_at: '2026-07-15 09:00' };
    const waiting = publishJob({
      id: 87,
      project_id: 1,
      account_id: boundAccount.id,
      action: 'publish',
      status: 'awaiting_confirmation',
      platform_draft_id: '',
      requires_confirmation: true,
    });
    api.drafts.mockResolvedValue({ drafts: [restoredDraft] });
    api.projectPublishJob.mockResolvedValue({ job: waiting });
    api.accounts
      .mockRejectedValueOnce(new Error('草稿账号恢复失败'))
      .mockResolvedValueOnce({ accounts: [authorizedAccount, boundAccount] });

    render(<Drafts />);

    await screen.findByText(restoredDraft.title);
    clickButton('发布');
    await screen.findByText('平台要求二次安全确认');
    expect(document.body.textContent).toContain('发布任务 ID：87');
    expect(screen.queryByText('发布状态刷新失败')).toBeNull();

    clickButton('刷新账号');
    await waitFor(() => expect(api.accounts).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByText(boundAccount.name).length).toBeGreaterThan(0));
    expect(document.body.textContent).toContain('发布任务 ID：87');
    expect(screen.queryByText('继续确认发布')).toBeNull();
  });

  it('only offers publishing after layout and shows awaiting confirmation without second-scan resume', async () => {
    api.drafts.mockResolvedValue({
      drafts: [
        { id: 1, project_id: 1, title: '正文草稿', stage: '生成文章', restore_stage: 'article', platform: '微信公众号', updated_at: '2026-07-14' },
        { id: 2, project_id: 2, title: '排版草稿', stage: '文章排版', restore_stage: 'layout', platform: '微信公众号', updated_at: '2026-07-14' },
      ],
    });
    api.createPublishJob.mockResolvedValue(publishJob({ project_id: 2, action: 'publish', status: 'failed', platform_draft_id: '', platform_url: '', failure_stage: '发布文章', failure_reason: '平台拒绝' }));
    api.retryPublishJob.mockResolvedValue(publishJob({ project_id: 2, action: 'publish', status: 'awaiting_confirmation', platform_draft_id: '', platform_url: '', requires_confirmation: true }));

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
    expect(document.body.textContent).toContain('当前版本不会发起二次扫码');
    expect(screen.queryByText('继续确认发布')).toBeNull();
    expect(api.resumePublishJob).not.toHaveBeenCalled();
  });

  it('does not let account refresh cancel a deferred publish response', async () => {
    const pendingPublish = deferred<PublishJob>();
    api.drafts.mockResolvedValue({
      drafts: [
        { id: 2, project_id: 2, title: '发布刷新竞态草稿', stage: '文章排版', restore_stage: 'layout', platform: '微信公众号', updated_at: '2026-07-14' },
      ],
    });
    api.createPublishJob.mockImplementation(() => pendingPublish.promise);

    render(<Drafts />);

    await screen.findByText('发布刷新竞态草稿');
    clickButton('发布');
    await waitFor(() => expect(api.accounts).toHaveBeenCalledTimes(1));
    clickButton('一键发布');
    await waitFor(() => expect(api.createPublishJob).toHaveBeenCalledTimes(1));

    const refreshAccounts = buttonsWithText('刷新账号')[0];
    expect(refreshAccounts.disabled).toBe(true);
    fireEvent.click(refreshAccounts);
    expect(api.accounts).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingPublish.resolve(publishJob({
        id: 88,
        project_id: 2,
        action: 'publish',
        status: 'published',
        platform_draft_id: '',
        platform_publish_id: 'publish-88-refresh',
      }));
      await pendingPublish.promise;
    });

    await waitFor(() => expect(document.body.textContent).toContain('平台发布 ID：publish-88-refresh'));
  });

  it('keeps publish state scoped to the selected draft when responses finish out of order', async () => {
    const firstPublish = deferred<ReturnType<typeof publishJob>>();
    const secondPublish = deferred<ReturnType<typeof publishJob>>();
    api.drafts.mockResolvedValue({
      drafts: [
        { id: 1, project_id: 1, title: '草稿 A', stage: '文章排版', restore_stage: 'layout', platform: '微信公众号', updated_at: '2026-07-14' },
        { id: 2, project_id: 2, title: '草稿 B', stage: '文章排版', restore_stage: 'layout', platform: '微信公众号', updated_at: '2026-07-14' },
      ],
    });
    api.createPublishJob
      .mockImplementationOnce(() => firstPublish.promise)
      .mockImplementationOnce(() => secondPublish.promise);

    render(<Drafts />);

    await waitFor(() => expect(screen.queryByText('草稿 A')).not.toBeNull());
    fireEvent.click(buttonsWithText('发布')[0]);
    await waitFor(() => expect(screen.queryByText('发布配置 · 草稿 A')).not.toBeNull());
    clickButton('一键发布');
    await waitFor(() => expect(api.createPublishJob).toHaveBeenCalledTimes(1));
    clickButton('取消');

    fireEvent.click(buttonsWithText('发布')[1]);
    await waitFor(() => expect(screen.queryByText('发布配置 · 草稿 B')).not.toBeNull());
    clickButton('一键发布');
    await waitFor(() => expect(api.createPublishJob).toHaveBeenCalledTimes(2));

    secondPublish.resolve(publishJob({ id: 82, project_id: 2, action: 'publish', status: 'published', platform_draft_id: '', platform_publish_id: 'publish-B' }));
    await waitFor(() => expect(document.body.textContent).toContain('平台发布 ID：publish-B'));
    firstPublish.resolve(publishJob({ id: 81, project_id: 1, action: 'publish', status: 'failed', platform_draft_id: '', failure_reason: '草稿 A 失败' }));
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    expect(document.body.textContent).toContain('平台发布 ID：publish-B');
    expect(screen.queryByText('草稿 A 失败')).toBeNull();
  });

  it('keeps deferred account and restored-job responses owned by the selected draft', async () => {
    const firstAccounts = deferred<{ accounts: Array<typeof boundAccount> }>();
    api.drafts.mockResolvedValue({
      drafts: [
        { id: 1, project_id: 1, title: '账号竞态草稿 A', stage: '文章排版', restore_stage: 'layout', platform: '微信公众号', updated_at: '2026-07-14' },
        { id: 2, project_id: 2, title: '账号竞态草稿 B', stage: '文章排版', restore_stage: 'layout', platform: '微信公众号', updated_at: '2026-07-14' },
      ],
    });
    api.projectPublishJob.mockImplementation(async (projectId) => ({
      job: projectId === 1
        ? publishJob({ id: 93, project_id: 1, account_id: boundAccount.id })
        : null,
    }));
    api.accounts
      .mockImplementationOnce(() => firstAccounts.promise)
      .mockResolvedValueOnce({ accounts: [authorizedAccount] });
    api.createPublishJob.mockResolvedValue(publishJob({
      id: 94,
      project_id: 2,
      account_id: authorizedAccount.id,
      action: 'publish',
      status: 'published',
      platform_draft_id: '',
      platform_publish_id: 'publish-draft-B',
    }));

    render(<Drafts />);

    await screen.findByText('账号竞态草稿 A');
    fireEvent.click(buttonsWithText('发布')[0]);
    await waitFor(() => expect(api.accounts).toHaveBeenCalledTimes(1));
    clickButton('取消');

    fireEvent.click(buttonsWithText('发布')[1]);
    await waitFor(() => expect(screen.queryAllByText(authorizedAccount.name).length).toBeGreaterThan(0));

    await act(async () => {
      firstAccounts.resolve({ accounts: [boundAccount] });
      await firstAccounts.promise;
    });

    expect(screen.queryByText('发布任务 ID：93')).toBeNull();
    clickButton('一键发布');
    await waitFor(() => expect(api.createPublishJob).toHaveBeenCalledWith({
      project_id: 2,
      account_id: authorizedAccount.id,
      action: 'publish',
    }));
    await screen.findByText('平台发布已完成');
  });

  it('retains a polling job and shows awaiting confirmation through status refresh', async () => {
    api.drafts.mockResolvedValue({
      drafts: [
        { id: 2, project_id: 2, title: '轮询草稿', stage: '文章排版', restore_stage: 'layout', platform: '微信公众号', updated_at: '2026-07-14' },
      ],
    });
    api.createPublishJob.mockResolvedValue(publishJob({ id: 83, project_id: 2, action: 'publish', status: 'publishing', platform_draft_id: '' }));
    api.getPublishJob
      .mockRejectedValueOnce(new Error('临时网络失败'))
      .mockRejectedValueOnce(new Error('临时网络失败'))
      .mockResolvedValueOnce(publishJob({ id: 83, project_id: 2, action: 'publish', status: 'awaiting_confirmation', platform_draft_id: '', requires_confirmation: true }));

    render(<Drafts />);

    await waitFor(() => expect(screen.queryByText('轮询草稿')).not.toBeNull());
    clickButton('发布');
    await waitFor(() => expect(api.accounts).toHaveBeenCalled());
    clickButton('一键发布');
    await waitFor(() => expect(api.getPublishJob).toHaveBeenCalledTimes(2), { timeout: 4_500 });

    expect(document.body.textContent).toContain('发布任务 ID：83');
    expect(screen.queryByText('刷新发布状态')).not.toBeNull();
    clickButton('刷新发布状态');
    await screen.findByText('平台要求二次安全确认');
    expect(document.body.textContent).toContain('当前版本不会发起二次扫码');
    expect(screen.queryByText('继续确认发布')).toBeNull();
    expect(api.resumePublishJob).not.toHaveBeenCalled();
  }, 8_000);

  it('shows an awaiting-confirmation job without a second-scan action', async () => {
    api.drafts.mockResolvedValue({
      drafts: [
        { id: 2, project_id: 2, title: '确认竞态草稿', stage: '文章排版', restore_stage: 'layout', platform: '微信公众号', updated_at: '2026-07-14' },
      ],
    });
    api.createPublishJob.mockResolvedValue(publishJob({ id: 84, project_id: 2, action: 'publish', status: 'awaiting_confirmation', platform_draft_id: '', requires_confirmation: true }));

    render(<Drafts />);

    await waitFor(() => expect(screen.queryByText('确认竞态草稿')).not.toBeNull());
    clickButton('发布');
    await waitFor(() => expect(api.accounts).toHaveBeenCalled());
    clickButton('一键发布');
    await screen.findByText('平台要求二次安全确认');
    expect(screen.queryByText('刷新发布状态')).toBeNull();
    expect(screen.queryByText('继续确认发布')).toBeNull();
    expect(document.body.textContent).toContain('当前版本不会发起二次扫码');
    expect(api.resumePublishJob).not.toHaveBeenCalled();
  });
});

describe('Task publishing results', () => {
  it('shows xiaohongshu publish monitoring and job event logs', async () => {
    const task = scheduledTask(5, {
      platform: '小红书',
      account_name: '小红书账号',
      task_type: '小红书图文生成',
      topic: '小红书日志任务',
      status: '已发布',
      publish_action: 'publish',
      publish_job: publishJob({
        id: 95,
        platform: '小红书',
        action: 'publish',
        status: 'published',
        platform_draft_id: '',
        platform_publish_id: 'xhs-95',
        platform_url: 'https://creator.xiaohongshu.com/publish/success?note=95',
        events: [
          { id: 1, stage: 'checking_login', level: 'info', message: '开始检查平台登录状态', payload: {}, created_at: '2026-07-22 10:00:00' },
          { id: 2, stage: 'uploading_assets', level: 'info', message: '开始上传发布素材', payload: {}, created_at: '2026-07-22 10:00:01' },
          { id: 3, stage: 'published', level: 'info', message: '小红书发布成功', payload: {}, created_at: '2026-07-22 10:00:05' },
        ],
      }),
    });
    api.tasks.mockResolvedValue({ tasks: [task] });
    api.task.mockResolvedValue(task);
    api.publishMonitoring.mockResolvedValue({
      platform: '小红书',
      total_jobs: 3,
      published_jobs: 2,
      failed_jobs: 1,
      in_progress_jobs: 0,
      success_rate: 0.6667,
      avg_duration_seconds: 12.4,
      recent_failures: [
        { id: 94, failure_stage: '发布文章', failure_reason: '平台拒绝', updated_at: '2026-07-22 09:50:00' },
      ],
    });

    render(<Tasks />);

    await screen.findByText('小红书发布监控');
    expect(document.body.textContent).toContain('总任务 3');
    expect(document.body.textContent).toContain('成功率 66.67%');
    expect(document.body.textContent).toContain('最近失败：发布文章 / 平台拒绝');
    expect(api.publishMonitoring).toHaveBeenCalledWith('小红书');

    clickButton('详情');

    await screen.findByText('发布日志');
    expect(document.body.textContent).toContain('开始上传发布素材');
    expect(document.body.textContent).toContain('小红书发布成功');
  });

  it('orders tasks by execution time from newest to oldest', async () => {
    api.tasks.mockResolvedValue({
      tasks: [
        scheduledTask(1, { topic: '7月15日任务', execute_time: '2026-07-15 09:30', next_run_at: '2026-07-15 09:30' }),
        scheduledTask(2, { topic: '7月17日任务', execute_time: '2026-07-17 09:30', next_run_at: '2026-07-17 09:30' }),
        scheduledTask(3, { topic: '7月16日任务', execute_time: '2026-07-16 09:30', next_run_at: '2026-07-16 09:30' }),
      ],
    });

    render(<Tasks />);

    await screen.findByText('7月17日任务');
    const pageText = document.body.textContent || '';
    expect(pageText.indexOf('7月17日任务')).toBeLessThan(pageText.indexOf('7月16日任务'));
    expect(pageText.indexOf('7月16日任务')).toBeLessThan(pageText.indexOf('7月15日任务'));
  });

  it('keeps task loading active across the StrictMode effect remount', async () => {
    api.tasks.mockResolvedValue({
      tasks: [scheduledTask(3, { topic: '严格模式任务', status: '待执行' })],
    });

    render(<StrictMode><Tasks /></StrictMode>);

    await screen.findByText('严格模式任务');
  });

  it('defaults new scheduled tasks to one-click publish and keeps platform draft selectable', async () => {
    api.tasks.mockResolvedValue({ tasks: [] });

    render(<Tasks />);

    await waitFor(() => expect(api.accounts).toHaveBeenCalled());
    clickButton('新增任务');
    await screen.findByText('新增任务');
    const publishLabel = screen.getAllByText('发布方式', { exact: true }).find((node) => node.tagName.toLowerCase() === 'label');
    const publishItem = publishLabel?.closest('.ant-form-item');
    expect(publishItem?.textContent).toContain('一键发布');

    await selectFormOption('发布方式', '同步到平台草稿箱');

    expect(publishItem?.textContent).toContain('同步到平台草稿箱');
    expect(screen.queryAllByText('该功能未上线')).toHaveLength(0);
  });

  it('keeps offline Xiaohongshu accounts out of scheduled task account choices', async () => {
    api.tasks.mockResolvedValue({ tasks: [] });
    api.accounts.mockResolvedValue({
      accounts: [
        authorizedAccount,
        {
          id: 18,
          platform: '小红书',
          name: '离线小红书',
          auth_status: '已授权',
          login_status: '离线',
        },
      ],
    });

    render(<Tasks />);

    await waitFor(() => expect(api.accounts).toHaveBeenCalled());
    clickButton('新增任务');
    await screen.findByText('新增任务');
    const accountSelect = document.querySelector('#account_id') as HTMLElement;
    expect(accountSelect).not.toBeNull();
    fireEvent.mouseDown(accountSelect);

    expect(screen.queryAllByText(/授权公众号/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/离线小红书/)).toBeNull();
  });

  it('polls with the latest search and ignores an older response generation', async () => {
    const oldResponse = deferred<{ tasks: ScheduledTask[] }>();
    const freshResponse = deferred<{ tasks: ScheduledTask[] }>();
    let poll: (() => Promise<void>) | null = null;
    vi.spyOn(window, 'setInterval').mockImplementation(((callback: () => Promise<void>, delay?: number) => {
      if (delay === 10_000) poll = callback;
      return 1;
    }) as typeof window.setInterval);
    vi.spyOn(window, 'clearInterval').mockImplementation(() => {});
    api.tasks
      .mockReturnValueOnce(oldResponse.promise)
      .mockReturnValueOnce(freshResponse.promise);

    render(<Tasks />);
    await waitFor(() => expect(api.tasks).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByPlaceholderText('搜索主题或账号'), { target: { value: '最新筛选' } });
    expect(poll).not.toBeNull();
    let pollingPromise: Promise<void> | undefined;
    act(() => { pollingPromise = poll?.(); });
    await waitFor(() => expect(api.tasks).toHaveBeenCalledTimes(2));

    const pollingParams = api.tasks.mock.calls[1][0] as URLSearchParams;
    expect(pollingParams.get('q')).toBe('最新筛选');
    freshResponse.resolve({ tasks: [scheduledTask(2, { topic: '最新筛选任务', status: '待执行' })] });
    await act(async () => { await pollingPromise; });
    await screen.findByText('最新筛选任务');

    oldResponse.resolve({ tasks: [scheduledTask(1, { topic: '旧响应任务', status: '待执行' })] });
    await act(async () => { await oldResponse.promise; });
    expect(screen.queryByText('旧响应任务')).toBeNull();
    expect(screen.getByText('最新筛选任务')).not.toBeNull();
  });

  it('renders hydrated publish jobs without second-scan resume', async () => {
    const published = scheduledTask(1, {
      topic: '已发布任务',
      status: '已发布',
      publish_job: publishJob({ id: 91, action: 'publish', status: 'published', platform_draft_id: '', platform_publish_id: 'publish-91' }),
    });
    const waiting = scheduledTask(2, {
      topic: '待确认任务',
      publish_job: publishJob({ id: 92, project_id: 2, action: 'publish', status: 'awaiting_confirmation', platform_draft_id: '', platform_url: '', requires_confirmation: true }),
    });
    api.tasks.mockResolvedValue({ tasks: [published, waiting] });

    render(<Tasks />);

    await waitFor(() => expect(screen.queryByText('发布 ID：publish-91')).not.toBeNull());
    expect(screen.queryAllByText('待人工确认').length).toBeGreaterThan(0);
    expect(screen.queryAllByText('平台要求二次安全确认').length).toBeGreaterThan(0);
    expect(screen.queryByText('继续确认发布')).toBeNull();
    expect(api.resumePublishJob).not.toHaveBeenCalled();
  });

  it('retries a failed hydrated publish job instead of rerunning the scheduled task', async () => {
    const failed = scheduledTask(3, {
      topic: '发布失败任务',
      status: '失败',
      publish_job: publishJob({ id: 93, project_id: 3, action: 'publish', status: 'failed', platform_draft_id: '', failure_reason: '平台拒绝' }),
    });
    const retryResponse = publishJob({ id: 93, project_id: 3, action: 'publish', status: 'publishing', platform_draft_id: '', failure_reason: '不应采用 retry 返回值' });
    const refreshed = scheduledTask(3, {
      topic: '发布失败任务',
      status: '已发布',
      publish_job: publishJob({ id: 93, project_id: 3, action: 'publish', status: 'published', platform_draft_id: '', platform_publish_id: 'publish-93', failure_reason: '' }),
    });
    api.tasks.mockResolvedValueOnce({ tasks: [failed] }).mockResolvedValue({ tasks: [refreshed] });
    api.task.mockResolvedValue(refreshed);
    api.retryPublishJob.mockResolvedValue(retryResponse);

    render(<Tasks />);

    await waitFor(() => expect(screen.queryByText('平台拒绝')).not.toBeNull());
    clickButton('重试发布任务');

    await waitFor(() => expect(api.retryPublishJob).toHaveBeenCalledWith(93));
    expect(api.retryTasks).not.toHaveBeenCalled();
    expect(api.task).toHaveBeenCalledWith(3);
    await waitFor(() => expect(api.tasks.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(api.task.mock.invocationCallOrder[0]).toBeGreaterThan(api.retryPublishJob.mock.invocationCallOrder[0]);
    expect(api.tasks.mock.invocationCallOrder[1]).toBeGreaterThan(api.task.mock.invocationCallOrder[0]);
    expect(screen.queryByText('平台拒绝')).toBeNull();
    expect(screen.queryByText('不应采用 retry 返回值')).toBeNull();
    expect(screen.queryByText('发布 ID：publish-93')).not.toBeNull();
  });

  it('keeps legacy unsupported platform tasks visible but blocks editing them in V1', async () => {
    const legacy = scheduledTask(4, {
      platform: '视频号',
      account_id: 44,
      account_name: '旧视频号账号',
      task_type: '视频号内容发布',
      topic: '旧平台任务',
      status: '待执行',
    });
    api.tasks.mockResolvedValue({ tasks: [legacy] });

    render(<Tasks />);

    await waitFor(() => expect(screen.queryByText('旧平台任务')).not.toBeNull());
    expect(buttonsWithText('删除')).toHaveLength(1);
    clickButton('编辑');
    await waitFor(() => expect(screen.queryByText('V1 仅开放微信公众号和小红书图文任务，当前旧任务只能查看或删除，不能编辑提交。')).not.toBeNull());

    const submit = buttonsWithText('保存修改并进入待执行')[0];
    expect(submit).toBeDefined();
    expect(submit.disabled).toBe(true);
    fireEvent.click(submit);
    expect(api.updateTask).not.toHaveBeenCalled();
  });
});
