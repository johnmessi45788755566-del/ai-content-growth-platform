import { describe, expect, it } from 'vitest';
import { formatApiError } from '../src/services/api';

describe('formatApiError', () => {
  it('explains backend connection failures clearly', () => {
    const error = new TypeError('Failed to fetch');

    expect(formatApiError(error)).toContain('后端服务未启动');
  });
});
