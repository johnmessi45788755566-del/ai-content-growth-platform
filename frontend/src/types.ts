export type CreatorStep = 'title' | 'article' | 'image' | 'layout';
export type BackendStage = 'title' | 'article' | 'images' | 'layout' | 'publish';

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
  account_name: string;
  execute_time: string;
  next_run_at?: string;
  task_type: string;
  topic: string;
  article_requirements?: string;
  selected_title?: string;
  title_direction?: string;
  status: string;
  execution_mode?: 'once' | 'repeat';
  repeat_rule?: 'daily' | 'weekly' | '';
  weekly_day?: number | null;
  failure_stage?: string;
  failure_reason?: string;
  runs?: TaskRun[];
  content_project?: ContentProject;
  publish_package?: PublishPackage;
}

export interface TaskRun {
  id?: number;
  started_at?: string;
  status: string;
  failure_stage?: string;
  failure_reason?: string;
}

export interface PublishPackage {
  id?: number;
  platform?: string;
  account_name?: string;
}
