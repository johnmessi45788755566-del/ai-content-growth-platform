import { afterEach, describe, expect, it, vi } from 'vitest';
import { API_BASE, api } from '../src/services/api';

function jsonResponse(body: unknown) {
  return {
    ok: true,
    json: async () => body,
  } as Response;
}

describe('publish job API', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('creates a publish job with the selected project, account, and action', async () => {
    const fetch = vi.fn().mockResolvedValue(jsonResponse({ id: 8, status: 'platform_draft' }));
    vi.stubGlobal('fetch', fetch);

    await api.createPublishJob({ project_id: 3, account_id: 5, action: 'draft' });

    expect(fetch).toHaveBeenCalledWith(`${API_BASE}/api/publish-jobs`, expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ project_id: 3, account_id: 5, action: 'draft' }),
    }));
  });

  it('gets, retries, and resumes one publish job resource', async () => {
    const fetch = vi.fn().mockResolvedValue(jsonResponse({ id: 8, status: 'published' }));
    vi.stubGlobal('fetch', fetch);

    await api.getPublishJob(8);
    await api.retryPublishJob(8);
    await api.resumePublishJob(8);

    expect(fetch).toHaveBeenNthCalledWith(1, `${API_BASE}/api/publish-jobs/8`, expect.any(Object));
    expect(fetch).toHaveBeenNthCalledWith(2, `${API_BASE}/api/publish-jobs/8/retry`, expect.objectContaining({ method: 'POST' }));
    expect(fetch).toHaveBeenNthCalledWith(3, `${API_BASE}/api/publish-jobs/8/resume`, expect.objectContaining({ method: 'POST' }));
  });
});
