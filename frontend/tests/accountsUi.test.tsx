// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import Accounts from '../src/pages/Accounts';

const api = vi.hoisted(() => ({
  accountAuthStatus: vi.fn(),
  accounts: vi.fn(),
  cancelAccountAuth: vi.fn(),
  deleteAccount: vi.fn(),
  startAccountAuth: vi.fn(),
  syncAccount: vi.fn(),
  updateAccount: vi.fn(),
}));

vi.mock('@/services/api', () => ({ api }));

const account = {
  id: 7,
  platform: '微信公众号',
  name: '轮询成功账号',
  external_id: 'bizuin-7',
  owner: '未分配',
  tag: '',
  auth_status: '已授权',
  login_status: '在线',
  monitor_status: '监测中',
  sync_status: '待同步',
  sync_error: '',
  role: '',
  audience: '',
  style: '',
  taboo: '',
  preference: '',
};

let intervalCallback: (() => Promise<void>) | null;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({
      matches: false,
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
});

beforeEach(() => {
  vi.resetAllMocks();
  intervalCallback = null;
  api.accounts.mockResolvedValue({ accounts: [] });
  api.cancelAccountAuth.mockResolvedValue({});
  vi.spyOn(window, 'setInterval').mockImplementation(((callback: () => Promise<void>) => {
    intervalCallback = callback;
    return 1;
  }) as typeof window.setInterval);
  vi.spyOn(window, 'clearInterval').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function startWechatLogin() {
  render(<Accounts />);
  await waitFor(() => expect(api.accounts).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
  fireEvent.click(await screen.findByRole('button', { name: /微信公众号/ }));
  await screen.findByText('等待微信公众号扫码登录');
  expect(intervalCallback).not.toBeNull();
}

describe('Accounts authentication', () => {
  it('starts with a waiting session and polls until the real account succeeds', async () => {
    api.startAccountAuth.mockResolvedValue({
      session_id: 'session-success',
      platform: '微信公众号',
      status: 'waiting',
      message: '等待扫码登录',
      login_url: 'https://mp.weixin.qq.com/',
      account: null,
      error: '',
    });
    api.accountAuthStatus.mockResolvedValue({
      session_id: 'session-success',
      platform: '微信公众号',
      status: 'success',
      message: '登录成功',
      login_url: 'https://mp.weixin.qq.com/cgi-bin/home',
      account: { id: 7, platform: '微信公众号', name: '轮询成功账号', auth_status: '已授权', login_status: '在线' },
      error: '',
    });
    api.accounts
      .mockResolvedValueOnce({ accounts: [] })
      .mockResolvedValue({ accounts: [account] });

    await startWechatLogin();
    await act(async () => { await intervalCallback?.(); });

    await waitFor(() => expect(screen.queryByText('等待微信公众号扫码登录')).toBeNull());
    expect(screen.queryByText('已登录账号：轮询成功账号')).toBeNull();
    expect(api.accountAuthStatus).toHaveBeenCalledWith('session-success');
    expect(api.accounts).toHaveBeenCalledTimes(2);
  });

  it('closes the login modal immediately after a successful scan login', async () => {
    api.startAccountAuth.mockResolvedValue({
      session_id: 'session-close',
      platform: '小红书',
      status: 'waiting',
      message: '等待扫码登录',
      login_url: 'https://creator.xiaohongshu.com/new/home',
      account: null,
      error: '',
    });
    api.accountAuthStatus.mockResolvedValue({
      session_id: 'session-close',
      platform: '小红书',
      status: 'success',
      message: '登录成功',
      login_url: 'https://creator.xiaohongshu.com/new/home',
      account: { id: 8, platform: '小红书', name: '小红书账号', auth_status: '已授权', login_status: '在线' },
      error: '',
    });
    api.accounts
      .mockResolvedValueOnce({ accounts: [] })
      .mockResolvedValue({ accounts: [{ ...account, id: 8, platform: '小红书', name: '小红书账号' }] });

    render(<Accounts />);
    await waitFor(() => expect(api.accounts).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(await screen.findByRole('button', { name: /小红书/ }));
    await screen.findByText('等待小红书扫码登录');
    await act(async () => { await intervalCallback?.(); });

    await waitFor(() => expect(screen.queryByText('等待小红书扫码登录')).toBeNull());
    expect(screen.queryByText('已登录账号：小红书账号')).toBeNull();
    expect(api.accounts).toHaveBeenCalledTimes(2);
  });

  it('stops polling and displays a timeout failure', async () => {
    api.startAccountAuth.mockResolvedValue({
      session_id: 'session-timeout',
      platform: '微信公众号',
      status: 'waiting',
      message: '等待扫码登录',
      login_url: 'https://mp.weixin.qq.com/',
      account: null,
      error: '',
    });
    api.accountAuthStatus.mockResolvedValue({
      session_id: 'session-timeout',
      platform: '微信公众号',
      status: 'failed',
      message: '登录超时，请重新发起登录',
      login_url: 'https://mp.weixin.qq.com/',
      account: null,
      error: '登录超时，请重新发起登录',
    });

    await startWechatLogin();
    await act(async () => { await intervalCallback?.(); });

    expect((await screen.findAllByText('登录超时，请重新发起登录')).length).toBeGreaterThan(0);
    expect(window.clearInterval).toHaveBeenCalled();
    expect(api.accounts).toHaveBeenCalledTimes(1);
  });

  it('ignores a cancelled session response after a newer login session starts', async () => {
    const staleStatus = deferred<{
      session_id: string;
      platform: string;
      status: 'success';
      message: string;
      login_url: string;
      account: { id: number; platform: string; name: string; auth_status: string; login_status: string };
      error: string;
    }>();
    api.startAccountAuth
      .mockResolvedValueOnce({
        session_id: 'session-a',
        platform: '微信公众号',
        status: 'waiting',
        message: '等待扫码登录',
        login_url: 'https://mp.weixin.qq.com/',
        account: null,
        error: '',
      })
      .mockResolvedValueOnce({
        session_id: 'session-b',
        platform: '微信公众号',
        status: 'waiting',
        message: '等待扫码登录',
        login_url: 'https://mp.weixin.qq.com/',
        account: null,
        error: '',
      });
    api.accountAuthStatus.mockImplementation(() => staleStatus.promise);

    await startWechatLogin();
    const pollA = intervalCallback;
    let pollAPromise: Promise<void> | undefined;
    act(() => { pollAPromise = pollA?.(); });
    await waitFor(() => expect(api.accountAuthStatus).toHaveBeenCalledWith('session-a'));

    const cancelButton = screen.getAllByRole('button').find(
      (button) => button.textContent?.replace(/\s/g, '') === '取消',
    );
    expect(cancelButton).toBeDefined();
    fireEvent.click(cancelButton as HTMLElement);
    await waitFor(() => expect(api.cancelAccountAuth).toHaveBeenCalledWith('session-a'));
    await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(await screen.findByRole('button', { name: /微信公众号/ }));
    await screen.findByText('等待微信公众号扫码登录');

    staleStatus.resolve({
      session_id: 'session-a',
      platform: '微信公众号',
      status: 'success',
      message: 'A 登录成功',
      login_url: 'https://mp.weixin.qq.com/cgi-bin/home',
      account: { id: 8, platform: '微信公众号', name: 'A 账号', auth_status: '已授权', login_status: '在线' },
      error: '',
    });
    await act(async () => { await pollAPromise; });

    expect(screen.queryByText('已登录账号：A 账号')).toBeNull();
    expect(screen.getByText('等待微信公众号扫码登录')).not.toBeNull();
    expect(api.accounts).toHaveBeenCalledTimes(1);
    expect(api.accountAuthStatus).not.toHaveBeenCalledWith('session-b');
  });

  it('shows the backend conflict when a referenced account cannot be deleted', async () => {
    api.accounts.mockResolvedValue({ accounts: [account] });
    api.deleteAccount.mockRejectedValue(new Error('账号已被发布任务或定时任务引用，不能删除'));

    render(<Accounts />);
    await screen.findByText(account.name);
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await screen.findByText(`确定删除账号「${account.name}」吗？`);
    fireEvent.click(screen.getByRole('button', { name: 'OK' }));

    await screen.findByText('账号已被发布任务或定时任务引用，不能删除');
    expect(api.deleteAccount).toHaveBeenCalledWith(account.id);
  });

  it('reopens scan login for an offline Xiaohongshu account from the matrix row', async () => {
    const offlineAccount = {
      ...account,
      id: 18,
      platform: '小红书',
      name: '离线小红书',
      login_status: '离线',
      sync_status: '同步失败',
      sync_error: '平台登录态已失效，请重新扫码登录',
    };
    api.accounts.mockResolvedValue({ accounts: [offlineAccount] });
    api.startAccountAuth.mockResolvedValue({
      session_id: 'xhs-relogin',
      platform: '小红书',
      status: 'waiting',
      message: '等待扫码登录',
      login_url: 'https://creator.xiaohongshu.com/new/home',
      account: null,
      error: '',
    });

    render(<Accounts />);
    fireEvent.click(await screen.findByRole('button', { name: '离线小红书' }));

    await screen.findByText('等待小红书扫码登录');
    expect(api.startAccountAuth).toHaveBeenCalledWith({
      platform: '小红书',
      account_name: '离线小红书',
      account_id: 18,
    });
  });

  it('only offers online and offline login status filters', async () => {
    api.accounts.mockResolvedValue({ accounts: [account] });

    render(<Accounts />);
    await screen.findByText(account.name);
    fireEvent.mouseDown(screen.getByText('全部登录状态'));
    const optionTexts = Array.from(document.querySelectorAll('.ant-select-item-option-content'))
      .map((node) => node.textContent || '');

    expect(optionTexts).toContain('在线');
    expect(optionTexts).toContain('离线');
    expect(optionTexts).not.toContain('检测中');
    expect(optionTexts).not.toContain('未知');
  });

  it('syncs one account into analytics data from the matrix row', async () => {
    const refreshListener = vi.fn();
    window.addEventListener('analytics-refresh', refreshListener);
    api.accounts.mockResolvedValue({ accounts: [account] });
    api.syncAccount.mockResolvedValue({ synced: true, account });

    render(<Accounts />);
    await screen.findByText(account.name);
    fireEvent.click(screen.getByRole('button', { name: '同步' }));

    await screen.findByText('账号数据已同步到数据分析');
    expect(api.syncAccount).toHaveBeenCalledWith(account.id);
    expect(window.localStorage.getItem('analytics-refresh-at')).toBeTruthy();
    expect(refreshListener).toHaveBeenCalledTimes(1);
    window.removeEventListener('analytics-refresh', refreshListener);
  });
});
