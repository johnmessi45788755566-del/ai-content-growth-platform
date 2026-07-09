import type {
  ArticleBlock,
  ContentProject,
  Draft,
  LayoutBlock,
  MediaAsset,
  ScheduledTask,
  TopicDirection,
} from '@/types';

export const API_BASE = process.env.UMI_APP_API_BASE || 'http://127.0.0.1:8000';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || '接口请求失败');
  return data as T;
}

export function assetUrl(url = '') {
  if (!url) return '';
  return url.startsWith('http') || url.startsWith('data:') ? url : `${API_BASE}${url}`;
}

export const api = {
  topicRecommendations(user_prompt: string) {
    return request<{ directions: TopicDirection[]; assistant_message: string }>('/api/topic-recommendations', {
      method: 'POST',
      body: JSON.stringify({ user_prompt }),
    });
  },
  createProject(payload: { title: string; title_direction?: string; platform?: string }) {
    return request<ContentProject>('/api/content-projects', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  getProject(id: number) {
    return request<ContentProject>(`/api/content-projects/${id}`);
  },
  updateStage(id: number, stage: string) {
    return request<{
      project: ContentProject;
      article_blocks: ArticleBlock[];
      media_assets: MediaAsset[];
      layout_blocks: LayoutBlock[];
    }>(`/api/content-projects/${id}/stage`, {
      method: 'PATCH',
      body: JSON.stringify({ stage }),
    });
  },
  generateArticle(id: number) {
    return request<{ project: ContentProject; blocks: ArticleBlock[] }>(`/api/content-projects/${id}/generate-article`, {
      method: 'POST',
    });
  },
  saveArticle(id: number, blocks: ArticleBlock[]) {
    return request<{ project: ContentProject; blocks: ArticleBlock[] }>(`/api/content-projects/${id}/article`, {
      method: 'PATCH',
      body: JSON.stringify({ blocks }),
    });
  },
  generateImages(id: number, asset_type?: 'cover' | 'inline') {
    return request<{ project: ContentProject; assets: MediaAsset[]; generated_assets: MediaAsset[] }>(
      `/api/content-projects/${id}/generate-images`,
      {
        method: 'POST',
        body: asset_type ? JSON.stringify({ asset_type }) : undefined,
      },
    );
  },
  selectMedia(id: number, selected_asset_ids: number[]) {
    return request<{ project: ContentProject; assets: MediaAsset[] }>(`/api/content-projects/${id}/media-selection`, {
      method: 'PATCH',
      body: JSON.stringify({ selected_asset_ids }),
    });
  },
  saveLayout(id: number, blocks: LayoutBlock[]) {
    return request<{ project: ContentProject; layout_blocks: LayoutBlock[] }>(`/api/content-projects/${id}/layout`, {
      method: 'PATCH',
      body: JSON.stringify({ blocks }),
    });
  },
  publishPackage(id: number, payload: { platform: string; account_name: string }) {
    return request<{ package: { id: number } }>(`/api/content-projects/${id}/publish-package`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  drafts(params: URLSearchParams) {
    const query = params.toString();
    return request<{ drafts: Draft[] }>(`/api/drafts${query ? `?${query}` : ''}`);
  },
  deleteDraft(id: number) {
    return request(`/api/drafts/${id}`, { method: 'DELETE' });
  },
  tasks(params: URLSearchParams) {
    const query = params.toString();
    return request<{ tasks: ScheduledTask[] }>(`/api/tasks${query ? `?${query}` : ''}`);
  },
  createTask(payload: unknown) {
    return request<ScheduledTask>('/api/tasks', { method: 'POST', body: JSON.stringify(payload) });
  },
  updateTask(id: number, payload: unknown) {
    return request<ScheduledTask>(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(payload) });
  },
  deleteTask(id: number) {
    return request(`/api/tasks/${id}`, { method: 'DELETE' });
  },
  task(id: number) {
    return request<ScheduledTask>(`/api/tasks/${id}`);
  },
  retryTasks(task_ids: number[]) {
    return request('/api/tasks/retry', { method: 'POST', body: JSON.stringify({ task_ids }) });
  },
};
