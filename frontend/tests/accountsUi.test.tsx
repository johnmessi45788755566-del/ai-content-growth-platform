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
  monitor_status: '监测中',
  sync_status: '待同步',
  role: '',
  audience: '',
  style: '',
  taboo: '',
  preference: '',
};

let intervalCallback: (() => Promise<void>) | null;

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
      account: { id: 7, platform: '微信公众号', name: '轮询成功账号', auth_status: '已授权' },
      error: '',
    });
    api.accounts
      .mockResolvedValueOnce({ accounts: [] })
      .mockResolvedValue({ accounts: [account] });

    await startWechatLogin();
    await act(async () => { await intervalCallback?.(); });

    await screen.findByText('已登录账号：轮询成功账号');
    expect(api.accountAuthStatus).toHaveBeenCalledWith('session-success');
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
});
