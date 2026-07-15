import { Alert, Button, Card, Col, Empty, Form, Image, Input, Modal, Row, Select, Space, Spin, Steps, message } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, assetUrl } from '@/services/api';
import type { ArticleBlock, ContentProject, CreatorStep, LayoutBlock, MediaAsset, PlatformAccount, PublishAction, PublishJob, TopicDirection } from '@/types';

const { TextArea } = Input;

function backendStage(step: CreatorStep) {
  return step === 'image' ? 'images' : step;
}

function frontendStage(stage = 'title'): CreatorStep {
  return stage === 'images' ? 'image' : (stage as CreatorStep);
}

function isIntroBlock(block: ArticleBlock, index: number) {
  return index === 0 || block.block_type === 'intro' || block.type === 'intro' || block.label === '引言';
}

const publishTerminalStatuses = ['platform_draft', 'published', 'awaiting_confirmation', 'failed'];
const maxPublishPollFailures = 2;

async function waitForPublishJob(
  initialJob: PublishJob,
  isCurrent: () => boolean,
  onJob: (job: PublishJob) => void,
) {
  let job = initialJob;
  let consecutiveFailures = 0;
  while (!publishTerminalStatuses.includes(job.status)) {
    await new Promise((resolve) => window.setTimeout(resolve, 1500));
    if (!isCurrent()) return null;
    try {
      const nextJob = await api.getPublishJob(job.id);
      if (!isCurrent()) return null;
      if (nextJob.project_id !== initialJob.project_id) throw new Error('发布任务归属与当前项目不一致');
      job = nextJob;
      consecutiveFailures = 0;
      onJob(job);
    } catch (error) {
      consecutiveFailures += 1;
      if (consecutiveFailures >= maxPublishPollFailures) throw error;
    }
  }
  return job;
}

export default function Creator() {
  const [step, setStep] = useState<CreatorStep>('title');
  const [projectId, setProjectId] = useState<number | null>(null);
  const [selectedTitle, setSelectedTitle] = useState('');
  const [titleDirection, setTitleDirection] = useState('');
  const [topicPrompt, setTopicPrompt] = useState('');
  const [topicDirections, setTopicDirections] = useState<TopicDirection[]>([]);
  const [articleBlocks, setArticleBlocks] = useState<ArticleBlock[]>([]);
  const [mediaAssets, setMediaAssets] = useState<MediaAsset[]>([]);
  const [layoutBlocks, setLayoutBlocks] = useState<LayoutBlock[]>([]);
  const [layoutImageSelections, setLayoutImageSelections] = useState<Record<number, number | undefined>>({});
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishAccounts, setPublishAccounts] = useState<PlatformAccount[]>([]);
  const [publishAccountsLoading, setPublishAccountsLoading] = useState(false);
  const [publishJob, setPublishJob] = useState<PublishJob | null>(null);
  const [publishingAction, setPublishingAction] = useState<PublishAction | ''>('');
  const [publishPollingError, setPublishPollingError] = useState('');
  const [refreshingPublishJob, setRefreshingPublishJob] = useState(false);
  const [publishForm] = Form.useForm();
  const [loading, setLoading] = useState('');
  const [imageLoadingType, setImageLoadingType] = useState('');
  const activeProjectIdRef = useRef<number | null>(null);
  const publishModalProjectIdRef = useRef<number | null>(null);
  const publishAccountsProjectIdRef = useRef<number | null>(null);
  const publishRequestGenerationRef = useRef(0);
  const currentPublishJob = publishJob?.project_id === projectId ? publishJob : null;

  function invalidatePublishRequest() {
    publishRequestGenerationRef.current += 1;
    setPublishingAction('');
    setRefreshingPublishJob(false);
  }

  function beginPublishRequest(targetProjectId: number) {
    const generation = publishRequestGenerationRef.current + 1;
    publishRequestGenerationRef.current = generation;
    setPublishingAction('');
    setRefreshingPublishJob(false);
    setPublishPollingError('');
    return { projectId: targetProjectId, generation };
  }

  function isPublishRequestCurrent(request: { projectId: number; generation: number }) {
    return activeProjectIdRef.current === request.projectId
      && publishModalProjectIdRef.current === request.projectId
      && publishRequestGenerationRef.current === request.generation;
  }

  function acceptPublishJob(job: PublishJob, request: { projectId: number; generation: number }) {
    if (!isPublishRequestCurrent(request)) return false;
    if (job.project_id !== request.projectId) throw new Error('发布任务归属与当前项目不一致');
    setPublishJob(job);
    return true;
  }

  function applyProject(project?: ContentProject) {
    if (!project) return;
    if (activeProjectIdRef.current !== project.id) {
      activeProjectIdRef.current = project.id;
      publishModalProjectIdRef.current = null;
      publishAccountsProjectIdRef.current = null;
      invalidatePublishRequest();
      setPublishAccounts([]);
      setPublishAccountsLoading(false);
      setPublishJob(null);
      setPublishPollingError('');
    }
    setProjectId(project.id);
    setSelectedTitle(project.title || '');
    setTitleDirection(project.title_direction || '');
    setArticleBlocks(project.article_blocks || []);
    setMediaAssets(project.media_assets || []);
    setLayoutBlocks(project.layout_blocks || []);
    if (project.current_stage) setStep(frontendStage(project.current_stage));
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const projectIdFromQuery = Number(params.get('project_id'));
    const stageFromQuery = params.get('stage');
    if (!projectIdFromQuery) return;
    api.getProject(projectIdFromQuery)
      .then((project) => {
        applyProject(project);
        if (stageFromQuery) setStep(frontendStage(stageFromQuery));
      })
      .catch((error) => message.error(error instanceof Error ? error.message : '恢复草稿失败'));
  }, []);

  async function saveStage(nextStep: CreatorStep) {
    if (!projectId) return;
    const data = await api.updateStage(projectId, backendStage(nextStep));
    applyProject(data.project);
    setArticleBlocks(data.article_blocks || []);
    setMediaAssets(data.media_assets || []);
    setLayoutBlocks(data.layout_blocks || []);
  }

  async function goStep(nextStep: CreatorStep) {
    try {
      if (projectId && step === 'article') await saveArticle();
      setStep(nextStep);
      closePublishModal();
      if (projectId) await saveStage(nextStep);
      setStep(nextStep);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '切换失败');
    }
  }

  async function generateTopics() {
    if (!topicPrompt.trim()) {
      message.warning('请先输入想写的内容');
      return;
    }
    setLoading('topics');
    try {
      const data = await api.topicRecommendations(topicPrompt.trim());
      setTopicDirections(data.directions || []);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '生成选题失败');
    } finally {
      setLoading('');
    }
  }

  async function confirmTitle(title: string, direction: string) {
    setSelectedTitle(title);
    setTitleDirection(direction);
    setStep('article');
    setLoading('article');
    try {
      const project = await api.createProject({ title, title_direction: direction, platform: '微信公众号' });
      applyProject(project);
      const article = await api.generateArticle(project.id);
      applyProject(article.project);
      setArticleBlocks(article.blocks || article.project.article_blocks || []);
      setStep('article');
    } catch (error) {
      setStep('title');
      message.error(error instanceof Error ? error.message : '生成文章失败');
    } finally {
      setLoading('');
    }
  }

  async function saveArticle() {
    if (!projectId || !articleBlocks.length) return;
    const blocks = articleBlocks.map((block, index) => ({
      block_type: index === 0 ? 'intro' : 'body',
      label: index === 0 ? '引言' : '正文',
      content: block.content,
      sort_order: index + 1,
    }));
    const data = await api.saveArticle(projectId, blocks);
    applyProject(data.project);
    setArticleBlocks(data.blocks || []);
  }

  async function enterImageStep() {
    if (!projectId) {
      message.warning('请先确认标题');
      return;
    }
    setStep('image');
    setLoading('images');
    setImageLoadingType('all');
    try {
      await saveArticle();
      if (!mediaAssets.length) {
        const data = await api.generateImages(projectId);
        applyProject(data.project);
        setMediaAssets(data.assets || []);
      } else {
        await saveStage('image');
      }
      setStep('image');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '生成图片失败');
    } finally {
      setLoading('');
      setImageLoadingType('');
    }
  }

  async function generateImage(type: 'cover' | 'inline') {
    if (!projectId) {
      message.warning('请先确认标题');
      return;
    }
    setLoading('images');
    setImageLoadingType(type);
    try {
      const data = await api.generateImages(projectId, type);
      applyProject(data.project);
      setMediaAssets(data.assets || []);
      setStep('image');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '生成图片失败');
    } finally {
      setLoading('');
      setImageLoadingType('');
    }
  }

  async function selectImage(asset: MediaAsset) {
    if (!projectId) return;
    const nextAssets = mediaAssets.map((item) => {
      if (asset.asset_type === 'cover' && item.asset_type === 'cover') {
        return { ...item, selected: item.id === asset.id ? 1 : 0 };
      }
      if (item.id === asset.id) return { ...item, selected: Number(item.selected) === 1 ? 0 : 1 };
      return item;
    });
    setMediaAssets(nextAssets);
    try {
      const ids = nextAssets.filter((item) => Number(item.selected) === 1).map((item) => item.id);
      const data = await api.selectMedia(projectId, ids);
      applyProject(data.project);
      setMediaAssets(data.assets || []);
      setStep('image');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '选择图片失败');
    }
  }

  const selectedCover = useMemo(
    () => mediaAssets.filter((asset) => asset.asset_type === 'cover' && Number(asset.selected) === 1)[0] || mediaAssets.filter((asset) => asset.asset_type === 'cover').at(-1),
    [mediaAssets],
  );

  const inlineAssets = useMemo(() => mediaAssets.filter((asset) => asset.asset_type === 'inline'), [mediaAssets]);

  function layoutImageForBlock(index: number, bodyIndex: number) {
    const configured = layoutImageSelections[index];
    if (configured !== undefined) return inlineAssets.find((asset) => asset.id === configured);
    const selected = inlineAssets.filter((asset) => Number(asset.selected) === 1);
    return selected[bodyIndex] || inlineAssets[bodyIndex];
  }

  async function saveLayout() {
    if (!projectId) {
      message.warning('请先完成内容创作');
      return;
    }
    const blocks: LayoutBlock[] = [
      { section_type: 'title', content: selectedTitle, media_asset_id: selectedCover?.id || null, sort_order: 1 },
      ...articleBlocks.map((block, index) => {
        const bodyIndex = articleBlocks.slice(0, index).filter((item, itemIndex) => !isIntroBlock(item, itemIndex)).length;
        const isIntro = isIntroBlock(block, index);
        return {
          section_type: isIntro ? 'intro' : 'body',
          content: block.content,
          media_asset_id: isIntro ? null : layoutImageForBlock(index, bodyIndex)?.id || null,
          sort_order: index + 2,
        };
      }),
    ];
    const data = await api.saveLayout(projectId, blocks);
    applyProject(data.project);
    setLayoutBlocks(data.layout_blocks || []);
  }

  function commitPublishAccounts(
    targetProjectId: number,
    accounts: PlatformAccount[],
    preferredAccountId?: number,
    fallbackToFirst = false,
  ) {
    const currentId = publishForm.getFieldValue('account_id');
    let nextAccountId = accounts.some((account) => account.id === currentId)
      ? currentId
      : accounts[0]?.id;
    if (preferredAccountId !== undefined) {
      nextAccountId = accounts.some((account) => account.id === preferredAccountId)
        ? preferredAccountId
        : undefined;
    } else if (fallbackToFirst) {
      nextAccountId = accounts[0]?.id;
    }
    publishAccountsProjectIdRef.current = targetProjectId;
    setPublishAccounts(accounts);
    publishForm.setFieldValue('account_id', nextAccountId);
  }

  async function loadPublishAccounts() {
    const data = await api.accounts(new URLSearchParams({ auth_status: '已授权', platform: '微信公众号' }));
    return (data.accounts || []).filter((account) => account.platform === '微信公众号' && account.auth_status === '已授权');
  }

  async function restorePublishJob(targetProjectId: number) {
    const request = beginPublishRequest(targetProjectId);
    try {
      const resource = await api.projectPublishJob(targetProjectId);
      if (!isPublishRequestCurrent(request)) return;
      setPublishAccountsLoading(true);
      const accounts = await loadPublishAccounts();
      if (!isPublishRequestCurrent(request)) return;
      commitPublishAccounts(
        targetProjectId,
        accounts,
        resource.job?.account_id,
        resource.job === null,
      );
      setPublishAccountsLoading(false);
      const existing = resource.job;
      if (!existing) {
        setPublishJob(null);
        return;
      }
      if (!acceptPublishJob(existing, request)) return;
      await waitForPublishJob(
        existing,
        () => isPublishRequestCurrent(request),
        (job) => acceptPublishJob(job, request),
      );
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      const text = error instanceof Error ? error.message : '恢复发布状态失败';
      setPublishPollingError(text);
      message.error(text);
    } finally {
      if (isPublishRequestCurrent(request)) setPublishAccountsLoading(false);
    }
  }

  async function refreshPublishAccounts() {
    const targetProjectId = publishModalProjectIdRef.current;
    if (!targetProjectId) return;
    const request = beginPublishRequest(targetProjectId);
    setPublishAccountsLoading(true);
    try {
      const accounts = await loadPublishAccounts();
      if (!isPublishRequestCurrent(request)) return;
      commitPublishAccounts(targetProjectId, accounts);
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      message.error(error instanceof Error ? error.message : '加载授权账号失败');
    } finally {
      if (isPublishRequestCurrent(request)) setPublishAccountsLoading(false);
    }
  }

  async function openPublishModal() {
    try {
      await saveLayout();
      const currentProjectId = activeProjectIdRef.current;
      if (publishModalProjectIdRef.current !== currentProjectId) {
        publishModalProjectIdRef.current = currentProjectId;
        publishAccountsProjectIdRef.current = null;
        invalidatePublishRequest();
        setPublishAccounts([]);
        setPublishAccountsLoading(false);
        setPublishJob(null);
        setPublishPollingError('');
        publishForm.resetFields();
      }
      setPublishOpen(true);
      if (currentProjectId) await restorePublishJob(currentProjectId);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '保存排版失败');
    }
  }

  async function runPublishAction(action: PublishAction) {
    if (!projectId) return;
    const targetProjectId = projectId;
    const request = beginPublishRequest(targetProjectId);
    const values = await publishForm.validateFields() as { account_id: number };
    if (!isPublishRequestCurrent(request)) return;
    if (publishAccountsProjectIdRef.current !== targetProjectId) {
      message.error('所选账号不属于当前项目，请刷新账号');
      return;
    }
    const account = publishAccounts.find((item) => item.id === values.account_id);
    if (!account) {
      message.error('所选账号不存在或授权状态已变化，请刷新账号');
      return;
    }
    setPublishingAction(action);
    let acceptedJob = false;
    try {
      const created = await api.createPublishJob({
        project_id: targetProjectId,
        account_id: account.id,
        action,
      });
      acceptedJob = acceptPublishJob(created, request);
      if (!acceptedJob) return;
      await waitForPublishJob(
        created,
        () => isPublishRequestCurrent(request),
        (job) => acceptPublishJob(job, request),
      );
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      const text = error instanceof Error ? error.message : '发布失败';
      if (acceptedJob) setPublishPollingError(text);
      message.error(text);
    } finally {
      if (isPublishRequestCurrent(request)) setPublishingAction('');
    }
  }

  async function retryPublishJob() {
    if (!currentPublishJob) return;
    const targetProjectId = currentPublishJob.project_id;
    const request = beginPublishRequest(targetProjectId);
    setPublishingAction(currentPublishJob.action);
    try {
      const job = await api.retryPublishJob(currentPublishJob.id);
      if (!acceptPublishJob(job, request)) return;
      await waitForPublishJob(
        job,
        () => isPublishRequestCurrent(request),
        (nextJob) => acceptPublishJob(nextJob, request),
      );
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      const text = error instanceof Error ? error.message : '重试失败';
      setPublishPollingError(text);
      message.error(text);
    } finally {
      if (isPublishRequestCurrent(request)) setPublishingAction('');
    }
  }

  async function resumePublishJob() {
    if (!currentPublishJob) return;
    const targetProjectId = currentPublishJob.project_id;
    const request = beginPublishRequest(targetProjectId);
    setPublishingAction('publish');
    try {
      const job = await api.resumePublishJob(currentPublishJob.id);
      if (!acceptPublishJob(job, request)) return;
      await waitForPublishJob(
        job,
        () => isPublishRequestCurrent(request),
        (nextJob) => acceptPublishJob(nextJob, request),
      );
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      const text = error instanceof Error ? error.message : '继续发布失败';
      setPublishPollingError(text);
      message.error(text);
    } finally {
      if (isPublishRequestCurrent(request)) setPublishingAction('');
    }
  }

  async function refreshPublishJob() {
    if (!currentPublishJob) return;
    const request = beginPublishRequest(currentPublishJob.project_id);
    setRefreshingPublishJob(true);
    try {
      const job = await api.getPublishJob(currentPublishJob.id);
      if (!acceptPublishJob(job, request)) return;
      await waitForPublishJob(
        job,
        () => isPublishRequestCurrent(request),
        (nextJob) => acceptPublishJob(nextJob, request),
      );
    } catch (error) {
      if (!isPublishRequestCurrent(request)) return;
      const text = error instanceof Error ? error.message : '刷新发布状态失败';
      setPublishPollingError(text);
      message.error(text);
    } finally {
      if (isPublishRequestCurrent(request)) setRefreshingPublishJob(false);
    }
  }

  function closePublishModal() {
    invalidatePublishRequest();
    publishModalProjectIdRef.current = null;
    publishAccountsProjectIdRef.current = null;
    setPublishAccounts([]);
    setPublishAccountsLoading(false);
    publishForm.resetFields();
    setPublishOpen(false);
  }

  return (
    <>
      <Steps
        className="creator-stepper"
        current={['title', 'article', 'image', 'layout'].indexOf(step)}
        items={['标题确认', '生成文章', '生成图片', '文章排版'].map((title) => ({ title }))}
        onChange={(index) => goStep(['title', 'article', 'image', 'layout'][index] as CreatorStep)}
      />
      <div className="draft-tip">每一步都会实时自动保存到草稿箱，可从草稿恢复到对应步骤继续创作。</div>
      {step === 'title' && (
        <Card className="panel creator-chat" title="和 AI 说明你想写什么">
          <div className="chat-body">
            {!topicPrompt && !topicDirections.length && loading !== 'topics' && (
              <div className="chat-message assistant">
                <strong>AI 助手</strong>
                <p>告诉我你想写什么内容、目标客户是谁、产品卖点是什么、希望读者看完做什么。我会先给你推荐选题方向和标题。</p>
              </div>
            )}
            {topicPrompt && <div className="chat-message user"><strong>你</strong><p>{topicPrompt}</p></div>}
            {loading === 'topics' ? (
              <div className="chat-message assistant">
                <Spin size="small" />
                <p>正在生成选题方向和标题...</p>
              </div>
            ) : topicDirections.length ? (
              <div className="chat-message assistant wide">
                <strong>AI 推荐选题</strong>
                {topicDirections.map((direction) => (
                  <section className="topic-direction-card" key={direction.direction}>
                    <h3>{direction.direction}</h3>
                    <p className="muted">{direction.subtitle}</p>
                    <div className="title-suggestions">
                      {(direction.titles || []).map((title) => (
                        <Button key={title} className="suggestion-card" onClick={() => confirmTitle(title, direction.direction)}>
                          <span>推荐标题</span>
                          <strong>{title}</strong>
                        </Button>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            ) : (
              null
            )}
          </div>
          <Space.Compact className="chat-input-bar">
            <Input value={topicPrompt} onChange={(event) => setTopicPrompt(event.target.value)} placeholder="例如：写给本地餐饮老板，介绍暑期套餐如何提升到店复购" />
            <Button type="primary" loading={loading === 'topics'} onClick={generateTopics}>发送</Button>
          </Space.Compact>
        </Card>
      )}
      {step === 'article' && (
        <Card className="panel" title="公众号文章创作" extra={<span className="status info">{articleBlocks.length ? '草稿已自动保存' : '等待生成文章'}</span>}>
          <Form layout="vertical">
            <Form.Item label="已确认标题">
              <Input value={selectedTitle} onChange={(event) => setSelectedTitle(event.target.value)} />
            </Form.Item>
          </Form>
          {loading === 'article' ? (
            <Spin tip="AI 正在生成文章段落..." />
          ) : articleBlocks.length ? (
            <div className="article-sections">
              {articleBlocks.map((block, index) => (
                <div className="article-segment" key={index}>
                  <label>{index === 0 ? '引言' : `正文 ${index}`}</label>
                  <TextArea
                    value={block.content}
                    onChange={(event) => setArticleBlocks((prev) => prev.map((item, itemIndex) => itemIndex === index ? { ...item, content: event.target.value } : item))}
                  />
                </div>
              ))}
            </div>
          ) : (
            <Empty description="确认标题后，系统会自动生成公众号文章段落。" />
          )}
          <Space className="block-gap" style={{ width: '100%' }}>
            <Button onClick={() => goStep('title')}>上一步</Button>
            <Button type="primary" block disabled={!articleBlocks.length} onClick={enterImageStep}>文章完成，进入生成图片</Button>
          </Space>
        </Card>
      )}
      {step === 'image' && (
        <Card className="panel" title="图片生成" extra={<span className="muted">基于已确认标题和文章内容统一生成封面图和正文配图。</span>}>
          <ImageGallery title="公众号封面图" type="cover" assets={mediaAssets} loading={imageLoadingType} onGenerate={generateImage} onSelect={selectImage} />
          <ImageGallery title="正文配图" type="inline" assets={mediaAssets} loading={imageLoadingType} onGenerate={generateImage} onSelect={selectImage} />
          <Space className="block-gap" style={{ width: '100%' }}>
            <Button onClick={() => goStep('article')}>上一步</Button>
            <Button type="primary" block onClick={() => goStep('layout')}>图片完成，进入文章排版</Button>
          </Space>
        </Card>
      )}
      {step === 'layout' && (
        <Row gutter={16} align="top">
          <Col xs={24} xl={10}>
            <Card className="panel" title="编辑排版">
              <div className="layout-editor">
                <LayoutSection title="标题+封面图">
                  <Input value={selectedTitle} onChange={(event) => setSelectedTitle(event.target.value)} />
                  <SelectedImage asset={selectedCover} label="封面图" />
                </LayoutSection>
                <LayoutSection title="引言">
                  {articleBlocks.filter(isIntroBlock).map((block, index) => (
                    <div className="layout-block" key={index}>
                      <label>引言</label>
                      <TextArea value={block.content} onChange={(event) => setArticleBlocks((prev) => prev.map((item, itemIndex) => isIntroBlock(item, itemIndex) ? { ...item, content: event.target.value } : item))} />
                    </div>
                  ))}
                </LayoutSection>
                <LayoutSection title="正文+配图">
                  {articleBlocks.map((block, index) => ({ block, index })).filter(({ block, index }) => !isIntroBlock(block, index)).map(({ block, index }, bodyIndex) => (
                    <div className="layout-block" key={index}>
                      <label>正文 {bodyIndex + 1}</label>
                      <TextArea value={block.content} onChange={(event) => setArticleBlocks((prev) => prev.map((item, itemIndex) => itemIndex === index ? { ...item, content: event.target.value } : item))} />
                      <div className="paragraph-image-picker">
                        <Button onClick={() => document.querySelector<HTMLElement>(`[data-layout-image="${index}"]`)?.focus()}>+ {layoutImageForBlock(index, bodyIndex) ? '更换图片' : '添加图片'}</Button>
                        <Select
                          data-layout-image={index}
                          value={layoutImageForBlock(index, bodyIndex)?.id}
                          allowClear
                          placeholder="不插入图片"
                          onChange={(value) => setLayoutImageSelections((prev) => ({ ...prev, [index]: value }))}
                          options={inlineAssets.map((asset, assetIndex) => ({ value: asset.id, label: `正文配图 ${assetIndex + 1}` }))}
                        />
                        <SelectedImage asset={layoutImageForBlock(index, bodyIndex)} label="正文配图" />
                      </div>
                    </div>
                  ))}
                </LayoutSection>
              </div>
              <Space className="block-gap" style={{ width: '100%' }}>
                <Button onClick={() => goStep('image')}>上一步</Button>
                <Button type="primary" block onClick={openPublishModal}>发布</Button>
              </Space>
            </Card>
          </Col>
          <Col xs={24} xl={14}>
            <Card className="panel" title="排版预览">
              <div className="wechat-preview">
                {selectedCover ? <Image className="preview-cover-img" src={assetUrl(selectedCover.url)} alt="公众号封面图" /> : <Empty description="未选择封面图" />}
                <h2>{selectedTitle || '未确认标题'}</h2>
                {articleBlocks.map((block, index) => {
                  const bodyIndex = articleBlocks.slice(0, index).filter((item, itemIndex) => !isIntroBlock(item, itemIndex)).length;
                  const image = isIntroBlock(block, index) ? undefined : layoutImageForBlock(index, bodyIndex);
                  return (
                    <div key={index}>
                      {isIntroBlock(block, index) ? <blockquote>{block.content}</blockquote> : <p>{block.content}</p>}
                      {image && <Image className="preview-inline-img" src={assetUrl(image.url)} alt={`正文配图 ${bodyIndex + 1}`} />}
                    </div>
                  );
                })}
              </div>
            </Card>
          </Col>
        </Row>
      )}
      <Modal title="发布配置" open={publishOpen} onCancel={closePublishModal} footer={null}>
        <Form form={publishForm} layout="vertical">
          <Form.Item label="选择账号" name="account_id" rules={[{ required: true, message: '请选择已授权公众号账号' }]}>
            <Select
              disabled={Boolean(currentPublishJob)}
              loading={publishAccountsLoading}
              placeholder={publishAccounts.length ? '请选择已授权公众号账号' : '暂无已授权公众号账号，请先到账号矩阵登录'}
              options={publishAccounts.map((account) => ({ value: account.id, label: account.name }))}
            />
          </Form.Item>
          {currentPublishJob && (
            <Alert
              className="block-gap"
              showIcon
              type={currentPublishJob.status === 'failed' ? 'error' : currentPublishJob.status === 'awaiting_confirmation' ? 'warning' : ['platform_draft', 'published'].includes(currentPublishJob.status) ? 'success' : 'info'}
              message={currentPublishJob.status === 'platform_draft' ? '平台草稿已创建' : currentPublishJob.status === 'published' ? '平台发布已完成' : currentPublishJob.status === 'awaiting_confirmation' ? '等待运营人员确认' : currentPublishJob.status === 'failed' ? '平台发布失败' : '平台正在处理'}
              description={(
                <Space direction="vertical" size={4}>
                  <span>发布任务 ID：{currentPublishJob.id}</span>
                  {currentPublishJob.status === 'platform_draft' && <span>平台草稿 ID：{currentPublishJob.platform_draft_id || '-'}</span>}
                  {currentPublishJob.status === 'published' && <span>平台发布 ID：{currentPublishJob.platform_publish_id || '-'}</span>}
                  {currentPublishJob.platform_url && <a href={currentPublishJob.platform_url} target="_blank" rel="noreferrer">查看平台结果</a>}
                  {currentPublishJob.status === 'awaiting_confirmation' && <span>请由运营人员使用微信扫描平台弹出的确认二维码，完成后点击继续确认发布。</span>}
                  {currentPublishJob.status === 'awaiting_confirmation' && <Button type="primary" loading={publishingAction === 'publish'} onClick={resumePublishJob}>继续确认发布</Button>}
                  {currentPublishJob.status === 'failed' && <span>失败阶段：{currentPublishJob.failure_stage || '-'}<br />失败原因：{currentPublishJob.failure_reason || '-'}</span>}
                  {currentPublishJob.status === 'failed' && <Button loading={Boolean(publishingAction)} onClick={retryPublishJob}>重试</Button>}
                </Space>
              )}
            />
          )}
          {currentPublishJob && publishPollingError && (
            <Alert
              className="block-gap"
              showIcon
              type="warning"
              message="发布状态刷新失败"
              description={(
                <Space direction="vertical" size={4}>
                  <span>{publishPollingError}</span>
                  <Button loading={refreshingPublishJob} onClick={refreshPublishJob}>刷新发布状态</Button>
                </Space>
              )}
            />
          )}
          <Space wrap>
            <Button
              loading={publishingAction === 'draft'}
              disabled={!publishAccounts.length || Boolean(publishingAction) || Boolean(currentPublishJob && !['platform_draft', 'published'].includes(currentPublishJob.status))}
              onClick={() => runPublishAction('draft')}
            >
              同步到平台草稿箱
            </Button>
            <Button
              type="primary"
              loading={publishingAction === 'publish'}
              disabled={!publishAccounts.length || Boolean(publishingAction) || Boolean(currentPublishJob && !['platform_draft', 'published'].includes(currentPublishJob.status))}
              onClick={() => runPublishAction('publish')}
            >
              一键发布
            </Button>
            <Button disabled={Boolean(currentPublishJob)} loading={publishAccountsLoading} onClick={refreshPublishAccounts}>刷新账号</Button>
            <Button onClick={closePublishModal}>取消</Button>
          </Space>
        </Form>
      </Modal>
    </>
  );
}

function ImageGallery({
  title,
  type,
  assets,
  loading,
  onGenerate,
  onSelect,
}: {
  title: string;
  type: 'cover' | 'inline';
  assets: MediaAsset[];
  loading: string;
  onGenerate: (type: 'cover' | 'inline') => void;
  onSelect: (asset: MediaAsset) => void;
}) {
  const typed = assets.filter((asset) => asset.asset_type === type);
  const isLoading = loading === type || loading === 'all';
  return (
    <div className="image-gallery">
      <div className="panel-header">
        <h3>{title}</h3>
        <Button onClick={() => onGenerate(type)}>继续生图</Button>
      </div>
      <Image.PreviewGroup>
        <div className="image-options">
          {typed.map((asset, index) => (
            <div className={`image-option ${Number(asset.selected) === 1 ? 'active' : ''}`} key={asset.id}>
              <Image src={assetUrl(asset.url)} alt={`${title} ${index + 1}`} />
              <Button className="image-select-button" onClick={() => onSelect(asset)}>
                {Number(asset.selected) === 1 ? '已选择' : '选择'}
              </Button>
              {Number(asset.selected) === 1 && <span className="check-mark">✓</span>}
            </div>
          ))}
          {isLoading && <div className="loading-option"><Spin /><strong>图片正在生成中</strong><span>请稍候，精彩内容即将呈现</span></div>}
          {!typed.length && !isLoading && <Empty description={`暂无${title}`} />}
        </div>
      </Image.PreviewGroup>
    </div>
  );
}

function LayoutSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="layout-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function SelectedImage({ asset, label }: { asset?: MediaAsset; label: string }) {
  if (!asset) return <div className="selected-image-row"><span>{`暂未选择${label}`}</span></div>;
  return (
    <div className="selected-image-row">
      <Image src={assetUrl(asset.url)} alt={label} />
      <span>已选择{label}</span>
    </div>
  );
}
