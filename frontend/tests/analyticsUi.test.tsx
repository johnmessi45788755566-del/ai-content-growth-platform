// @vitest-environment jsdom

import { act, cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import Analytics from '../src/pages/Analytics';

const api = vi.hoisted(() => ({
  analyticsAccounts: vi.fn(),
  analyticsWorks: vi.fn(),
}));

vi.mock('@/services/api', () => ({ api }));

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
  api.analyticsAccounts.mockResolvedValue({ range: '30d', accounts: [] });
  api.analyticsWorks.mockResolvedValue({ range: '30d', works: [] });
});

afterEach(() => {
  cleanup();
});

describe('Analytics refresh', () => {
  it('reloads analytics rows after account matrix sync refresh event', async () => {
    render(<Analytics />);
    await waitFor(() => expect(api.analyticsAccounts).toHaveBeenCalledTimes(1));

    await act(async () => {
      window.dispatchEvent(new Event('analytics-refresh'));
    });

    await waitFor(() => expect(api.analyticsAccounts).toHaveBeenCalledTimes(2));
    expect(api.analyticsWorks).toHaveBeenCalledTimes(2);
  });
});
