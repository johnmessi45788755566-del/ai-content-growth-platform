export type CreatorStep = 'title' | 'article' | 'image' | 'layout';
export type BackendStage = 'title' | 'article' | 'images' | 'layout' | 'publish';
export type PublishAction = 'draft' | 'publish';
export type PublishJobStatus =
  | 'pending'
  | 'checking_login'
  | 'uploading_assets'
  | 'filling_content'
  | 'platform_draft'
  | 'publishing'
  | 'awaiting_confirmation'
  | 'published'
  | 'failed';

export interface TopicDirection {
  direction: string;
  subtitle?: string;
  titles: string[];
}

export interface ArticleBlock {
  id?: number;
  block_type?: string;
  type?: string;
  label?: string;
  content: string;
  sort_order?: number;
}

export interface MediaAsset {
  id: number;
  project_id?: number;
  asset_type: 'cover' | 'inline';
  url: string;
  local_path?: string;
  prompt?: string;
  selected: number | boolean;
}

export interface LayoutBlock {
  id?: number;
  section_type: string;
  content: string;
  media_asset_id?: number | null;
  sort_order?: number;
}

export interface ContentProject {
  id: number;
  title: string;
  title_direction?: string;
  platform: string;
  current_stage?: BackendStage;
  article_blocks?: ArticleBlock[];
  media_assets?: MediaAsset[];
  layout_blocks?: LayoutBlock[];
}

export interface Draft {
  id: number;
  project_id: number;
  title: string;
  stage: string;
  restore_stage: BackendStage;
  platform: string;
  updated_at: string;
}

export interface ScheduledTask {
  id: number;
  platform: string;
  account_id?: number | null;
  account_name: string;
  execute_time: string;
  next_run_at?: string;
  task_type: string;
  topic: string;
  article_requirements?: string;
  selected_title?: string;
  title_direction?: string;
  status: string;
  publish_action: PublishAction;
  publish_job_id?: number | null;
  publish_result?: string;
  execution_mode?: 'once' | 'repeat';
  repeat_rule?: 'daily' | 'weekly' | '';
  weekly_day?: number | null;
  failure_stage?: string;
  failure_reason?: string;
  runs?: TaskRun[];
  content_project?: ContentProject;
  publish_job?: PublishJob;
  publish_package?: PublishPackage;
}

export interface TaskRun {
  id?: number;
  started_at?: string;
  status: string;
  publish_job_id?: number | null;
  failure_stage?: string;
  failure_reason?: string;
}

export interface PublishJob {
  id: number;
  project_id: number;
  account_id: number;
  platform: '微信公众号';
  action: PublishAction;
  status: PublishJobStatus;
  platform_draft_id?: string;
  platform_publish_id?: string;
  platform_url?: string;
  requires_confirmation: boolean;
  failure_stage?: string;
  failure_reason?: string;
  created_at?: string;
  updated_at?: string;
}

export interface PublishPackage {
  id?: number;
  account_id?: number | null;
  platform?: string;
  account_name?: string;
  status?: string;
  platform_draft_id?: string;
  platform_draft_url?: string;
  failure_stage?: string;
  failure_reason?: string;
  published_at?: string;
}

export interface PlatformAccount {
  id: number;
  platform: string;
  name: string;
  external_id?: string;
  owner: string;
  tag: string;
  auth_status: string;
  monitor_status: string;
  sync_status: string;
  sync_error?: string;
  role: string;
  audience: string;
  style: string;
  taboo: string;
  preference: string;
  auth_method?: string;
  qr_url?: string;
  last_sync_at?: string;
  created_at?: string;
  updated_at?: string;
}

export interface AccountAuthSession {
  session_id: string;
  platform: string;
  status: 'waiting' | 'success' | 'failed' | 'cancelled';
  message: string;
  login_url: string;
  account?: PlatformAccount | null;
  error?: string;
}

export interface AccountAnalyticsRow {
  account_id: number;
  account_name: string;
  platform: string;
  owner?: string;
  tag?: string;
  fans: number;
  reads: number;
  recommends: number;
  likes: number;
  comments: number;
  shares: number;
  favorites: number;
  last_sync_at?: string;
}

export interface WorkAnalyticsRow {
  work_id: number;
  account_id: number;
  platform: string;
  account_name: string;
  title: string;
  content_type: string;
  status: string;
  reads: number;
  recommends: number;
  likes: number;
  comments: number;
  shares: number;
  favorites: number;
  published_at?: string;
  updated_at?: string;
}
