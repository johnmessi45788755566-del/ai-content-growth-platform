const state = {
  currentView: "home",
  analyticsTab: "account",
  selectedTaskMode: "new",
  creatorStep: "title",
  publishOpen: false,
  currentProjectId: null,
  selectedTitle: "",
  titleDirection: "",
  topicPrompt: "",
  topicDirections: [],
  articleBlocks: [],
  mediaAssets: [],
  layoutBlocks: [],
  layoutImageSelections: {},
  isGeneratingTopics: false,
  isGeneratingArticle: false,
  isGeneratingImages: false,
  loadingImageType: "",
  drafts: [],
  isLoadingDrafts: false,
  draftStartDate: "",
  draftEndDate: "",
  tasks: [],
  isLoadingTasks: false,
  taskStatusFilter: "",
  taskPlatformFilter: "",
  taskSearch: "",
  retrySelectMode: false,
  selectedRetryTaskIds: [],
};

const accounts = [
  {
    name: "AI记账助手",
    platform: "公众号",
    owner: "林晓",
    tag: "AI记账",
    auth: "已授权",
    monitor: "监测中",
    fans: "18,420",
    reads: "126,800",
    recommends: "298,000",
    likes: "6,180",
    comments: "920",
    shares: "1,146",
    favorites: "3,205",
    role: "AI 记账顾问",
    audience: "小微企业主、个体户",
    style: "专业、案例型、易懂",
    taboo: "夸大节税效果、承诺财税结果",
    preference: "标题中保留具体场景；优先使用经营问题切入",
  },
  {
    name: "小微账房",
    platform: "公众号",
    owner: "周宁",
    tag: "财税科普",
    auth: "已授权",
    monitor: "监测中",
    fans: "12,960",
    reads: "88,500",
    recommends: "174,300",
    likes: "4,520",
    comments: "611",
    shares: "936",
    favorites: "2,410",
    role: "小微企业财税助手",
    audience: "创业者、门店老板",
    style: "轻松、问答型",
    taboo: "复杂政策原文堆砌",
    preference: "问答标题表现更好；适合午间发布",
  },
  {
    name: "老板懂财务",
    platform: "公众号",
    owner: "陈沐",
    tag: "老板财务",
    auth: "授权失效",
    monitor: "暂停",
    fans: "9,360",
    reads: "45,100",
    recommends: "91,500",
    likes: "2,160",
    comments: "238",
    shares: "402",
    favorites: "1,086",
    role: "老板视角财务科普",
    audience: "企业老板、管理者",
    style: "直接、结论先行",
    taboo: "技术术语过多",
    preference: "短句和清单型内容收藏率更高",
  },
  {
    name: "财务自动化笔记",
    platform: "小红书",
    owner: "林晓",
    tag: "效率工具",
    auth: "未授权",
    monitor: "未监测",
    fans: "6,780",
    reads: "38,900",
    recommends: "112,600",
    likes: "3,420",
    comments: "367",
    shares: "290",
    favorites: "2,866",
    role: "AI 工具体验官",
    audience: "财务人员、运营人员",
    style: "清单型、实操型",
    taboo: "过度营销",
    preference: "封面突出对比数字时推荐数更高",
  },
];

const works = [
  ["AI记账助手", "公众号", "为什么小公司更需要 AI 记账", "图文", "38,200", "82,600", "2,180", "326", "512", "1,260", "已发布"],
  ["小微账房", "公众号", "老板每天看这 3 个账就够了", "图文", "29,600", "58,100", "1,630", "214", "428", "986", "已发布"],
  ["老板懂财务", "公众号", "现金流不是利润，先看这张表", "图文", "16,900", "31,400", "820", "97", "181", "420", "已发布"],
  ["财务自动化笔记", "小红书", "5 分钟搭一个自动记账表", "笔记", "22,400", "64,200", "2,920", "302", "246", "2,101", "已发布"],
];

const tasks = [
  ["公众号", "AI记账助手", "2026-07-08 09:30", "公众号文章创作", "AI 记账如何降低人工成本", "待执行"],
  ["公众号", "小微账房", "2026-07-08 13:00", "公众号文章创作", "老板如何看懂每日流水", "执行中"],
  ["公众号", "老板懂财务", "2026-07-07 18:00", "公众号文章创作", "现金流预警的三个信号", "失败"],
  ["小红书", "财务自动化笔记", "2026-07-10 20:00", "小红书文案", "自动化记账模板", "待执行"],
];

const pageTitles = {
  home: "首页",
  accounts: "账号矩阵",
  analytics: "数据分析",
  creator: "内容创作",
  drafts: "草稿箱",
  tasks: "任务中心",
  settings: "系统设置",
};

const viewRoot = document.querySelector("#viewRoot");
const pageTitle = document.querySelector("#pageTitle");
const drawer = document.querySelector("#drawer");
const drawerTitle = document.querySelector("#drawerTitle");
const drawerEyebrow = document.querySelector("#drawerEyebrow");
const drawerBody = document.querySelector("#drawerBody");
const creatorSubnav = document.querySelector("#creatorSubnav");
const API_BASE = "http://127.0.0.1:8000";
let autosaveTimer = null;
let taskFilterTimer = null;

function escapeHtml(value = "") {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "接口请求失败");
  return data;
}

function fullAssetUrl(url = "") {
  if (!url) return "";
  return url.startsWith("http") || url.startsWith("data:") ? url : `${API_BASE}${url}`;
}

function creatorBackendStage(step = state.creatorStep) {
  return step === "image" ? "images" : step;
}

function creatorFrontendStep(stage = "title") {
  return stage === "images" ? "image" : stage;
}

function selectedAssets(type) {
  return state.mediaAssets.filter((asset) => asset.asset_type === type && Number(asset.selected) === 1);
}

function selectedAsset(type) {
  const typed = state.mediaAssets.filter((asset) => asset.asset_type === type);
  return selectedAssets(type)[0] || typed[typed.length - 1] || null;
}

function inlineAssets() {
  return state.mediaAssets.filter((asset) => asset.asset_type === "inline");
}

function layoutImageForBlock(index) {
  const configuredId = state.layoutImageSelections[index];
  if (configuredId !== undefined) {
    return inlineAssets().find((asset) => String(asset.id) === String(configuredId)) || null;
  }
  return selectedAssets("inline")[index] || inlineAssets()[index] || null;
}

function showCreatorError(error) {
  window.alert(error.message || "操作失败");
}

function statusClass(value) {
  if (["已授权", "监测中", "成功", "已发布", "启用"].includes(value)) return "ok";
  if (["待执行", "执行中", "已重试"].includes(value)) return "info";
  if (["未授权", "未监测", "暂停"].includes(value)) return "warn";
  return "fail";
}

function metricCards(items) {
  return `<div class="grid cols-4">${items
    .map(
      (item) => `
        <div class="metric-card">
          <span>${item.label}</span>
          <strong>${item.value}</strong>
          <small>${item.note}</small>
        </div>`
    )
    .join("")}</div>`;
}

function table(headers, rows) {
  return `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>${headers.map((header) => `<th>${header}</th>`).join("")}</tr>
        </thead>
        <tbody>${rows.join("")}</tbody>
      </table>
    </div>`;
}

function renderHome() {
  const metrics = [
    { label: "已添加账号", value: "20", note: "公众号 12，小红书 5，视频号 3" },
    { label: "今日任务", value: "18", note: "待执行 11，执行中 3，失败 1" },
    { label: "今日生成内容", value: "36", note: "已发布 29，待发布 6" },
    { label: "AI 规则更新", value: "7", note: "4 条自动生效，3 条可回滚" },
  ];

  const taskRows = tasks
    .slice(0, 3)
    .map(
      (task) => `
      <tr>
        <td><span class="platform">${task[0]}</span></td>
        <td>${task[1]}</td>
        <td>${task[2]}</td>
        <td>${task[4]}</td>
        <td><span class="status ${statusClass(task[5])}">${task[5]}</span></td>
      </tr>`
    );

  return `
    ${metricCards(metrics)}
    <div class="grid cols-2 block-gap">
      <div class="panel">
        <div class="panel-header">
          <h3>账号分布</h3>
          <button class="ghost-button" data-view-link="accounts">查看账号</button>
        </div>
        <div class="chart-bars">
          ${bar("公众号", 72, "12 个")}
          ${bar("小红书", 38, "5 个")}
          ${bar("视频号", 24, "3 个")}
          ${bar("授权失效", 16, "2 个")}
        </div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <h3>数据摘要</h3>
          <button class="ghost-button" data-view-link="analytics">查看数据</button>
        </div>
        <div class="ai-rule-list">
          <div class="rule-item">
            <strong>AI记账助手 推荐数提升 18%</strong>
            <span class="muted">近 7 天案例型标题表现更好，AI 已更新标题偏好。</span>
          </div>
          <div class="rule-item">
            <strong>小微账房 收藏数提升 12%</strong>
            <span class="muted">清单型正文更容易被收藏，建议延续该结构。</span>
          </div>
        </div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header">
        <h3>今日任务</h3>
        <button class="primary-button" data-view-link="tasks">进入任务中心</button>
      </div>
      ${table(["平台", "账号", "执行时间", "主题", "状态"], taskRows)}
    </div>`;
}

function bar(label, percent, value) {
  return `
    <div class="bar-row">
      <span>${label}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${percent}%"></div></div>
      <strong>${value}</strong>
    </div>`;
}

function renderAccounts() {
  const rows = accounts.map(
    (account, index) => `
      <tr>
        <td><strong>${account.name}</strong><br><span class="muted">${account.role}</span></td>
        <td><span class="platform">${account.platform}</span></td>
        <td><span class="tag">${account.tag}</span></td>
        <td>${account.owner}</td>
        <td><span class="status ${statusClass(account.auth)}">${account.auth}</span></td>
        <td><span class="status ${statusClass(account.monitor)}">${account.monitor}</span></td>
        <td>2026-03-26 10:54</td>
        <td><button class="ghost-button" data-account="${index}">详情</button></td>
      </tr>`
  );

  return `
    ${metricCards([
      { label: "已添加", value: "20", note: "上限 1000 个账号" },
      { label: "已授权", value: "16", note: "2 个授权即将到期" },
      { label: "监测中", value: "14", note: "覆盖主要运营账号" },
      { label: "授权失效", value: "2", note: "需运营人处理" },
    ])}
    <div class="split-layout block-gap">
      <div class="panel">
        <div class="panel-header"><h3>平台分布</h3></div>
        <div class="chart-bars">
          ${bar("公众号", 78, "12")}
          ${bar("小红书", 42, "5")}
          ${bar("视频号", 26, "3")}
          ${bar("微博", 0, "0")}
        </div>
      </div>
      <div class="panel">
        <div class="toolbar">
          <div class="filters">
            <input class="input" placeholder="搜索账号名称 / ID" />
            <select class="select"><option>全部平台</option><option>公众号</option><option>小红书</option></select>
            <select class="select"><option>全部授权状态</option><option>已授权</option><option>授权失效</option></select>
            <select class="select"><option>全部运营人</option><option>林晓</option><option>周宁</option></select>
          </div>
          <div class="row-actions">
            <button class="secondary-button">授权检测</button>
            <button class="secondary-button">更新账号信息</button>
            <button class="primary-button">添加账号</button>
          </div>
        </div>
        ${table(["账号", "平台", "标签", "运营人", "授权状态", "监测状态", "添加时间", "操作"], rows)}
      </div>
    </div>`;
}

function renderAnalytics() {
  const tabButtons = `
    <div class="tabs">
      <button class="tab ${state.analyticsTab === "account" ? "active" : ""}" data-analytics-tab="account">账号分析</button>
      <button class="tab ${state.analyticsTab === "work" ? "active" : ""}" data-analytics-tab="work">作品分析</button>
      <button class="tab ${state.analyticsTab === "rule" ? "active" : ""}" data-analytics-tab="rule">AI 规则变更</button>
    </div>`;

  if (state.analyticsTab === "work") {
    const rows = works.map(
      (work) => `
      <tr>
        <td><strong>${work[2]}</strong></td>
        <td><span class="platform">${work[1]}</span></td>
        <td>${work[0]}</td>
        <td>${work[3]}</td>
        <td>${work[4]}</td>
        <td>${work[5]}</td>
        <td>${work[6]}</td>
        <td>${work[7]}</td>
        <td>${work[8]}</td>
        <td>${work[9]}</td>
        <td><span class="status ok">${work[10]}</span></td>
      </tr>`
    );
    return analyticsShell(
      tabButtons,
      table(["作品标题", "平台", "账号", "类型", "阅读 / 播放", "推荐数", "点赞", "评论", "转发", "收藏", "状态"], rows)
    );
  }

  if (state.analyticsTab === "rule") {
    return analyticsShell(
      tabButtons,
      `<div class="grid cols-2">
        <div class="panel">
          <div class="panel-header"><h3>规则变更记录</h3></div>
          <div class="ai-rule-list">
            ${rule("AI记账助手", "标题偏好", "推荐数高于账号均值 18%", "从功能说明型调整为经营痛点型")}
            ${rule("小微账房", "发布时间", "午间发布阅读量更稳定", "建议优先 12:00-13:30 发布")}
            ${rule("老板懂财务", "表达风格", "短句内容收藏率更高", "减少长段解释，增加清单结构")}
          </div>
        </div>
        <div class="panel">
          <div class="panel-header"><h3>变更详情</h3><button class="secondary-button">回滚</button></div>
          <div class="detail-grid">
            ${detail("账号", "AI记账助手")}
            ${detail("调整前", "标题以 AI 功能介绍为主")}
            ${detail("调整后", "标题以小微企业经营痛点为主")}
            ${detail("数据依据", "近 7 天推荐数、阅读量、收藏数均高于账号均值")}
            ${detail("生效时间", "2026-07-07 10:20")}
          </div>
        </div>
      </div>`
    );
  }

  const rows = accounts.map(
    (account) => `
      <tr>
        <td><strong>${account.name}</strong></td>
        <td><span class="platform">${account.platform}</span></td>
        <td>${account.fans}</td>
        <td>${account.reads}</td>
        <td>${account.recommends}</td>
        <td>${account.likes}</td>
        <td>${account.comments}</td>
        <td>${account.shares}</td>
        <td>${account.favorites}</td>
        <td>10 分钟前</td>
      </tr>`
  );
  return analyticsShell(
    tabButtons,
    table(["账号", "平台", "粉丝数", "阅读 / 播放", "推荐数", "点赞", "评论", "转发", "收藏", "更新时间"], rows)
  );
}

function analyticsShell(tabs, body) {
  return `
    <div class="toolbar">
      ${tabs}
      <div class="filters">
        <select class="select"><option>近 7 天</option><option>近 30 天</option></select>
        <select class="select"><option>全部平台</option><option>公众号</option><option>小红书</option></select>
        <input class="input" placeholder="搜索账号或作品" />
      </div>
    </div>
    ${metricCards([
      { label: "总推荐数", value: "676,400", note: "较上周期 +14.6%" },
      { label: "总阅读 / 播放", value: "299,300", note: "较上周期 +9.2%" },
      { label: "总互动", value: "25,814", note: "点赞、评论、转发、收藏" },
      { label: "规则变更", value: "7", note: "可查看依据并回滚" },
    ])}
    <div class="section-header"></div>
    ${body}`;
}

function rule(account, type, reason, result) {
  return `
    <div class="rule-item">
      <strong>${account} · ${type}</strong>
      <span class="muted">${reason}</span>
      <p>${result}</p>
      <button class="ghost-button">查看详情</button>
    </div>`;
}

function renderCreator() {
  const views = {
    title: renderCreatorTitle,
    article: renderCreatorArticle,
    image: renderCreatorImage,
    layout: renderCreatorLayout,
  };

  return `
    ${creatorStepper()}
    <div class="draft-tip">每一步都会实时自动保存到草稿箱，可从草稿恢复到对应步骤继续创作。</div>
    ${views[state.creatorStep]()}`;
}

function creatorStepper() {
  const steps = [
    ["title", "标题确认"],
    ["article", "生成文章"],
    ["image", "生成图片"],
    ["layout", "文章排版"],
  ];
  return `
    <div class="creator-stepper">
      ${steps
        .map(
          ([key, label]) => `
            <button class="creator-step ${state.creatorStep === key ? "active" : ""}" data-creator-step="${key}">
              ${label}
            </button>`
        )
        .join("")}
    </div>`;
}

function renderCreatorTitle() {
  const directions = state.topicDirections || [];
  const directionHtml = directions.length
    ? directions
        .map(
          (direction) => `
            <section class="topic-direction-card">
              <h4>${escapeHtml(direction.direction)}</h4>
              <p>${escapeHtml(direction.subtitle || "")}</p>
              <div class="title-suggestions">
                ${(direction.titles || [])
                  .map(
                    (title) => `
                      <button class="suggestion-card title-card" data-action="confirm-topic-title" data-title="${escapeHtml(title)}" data-direction="${escapeHtml(direction.direction)}">
                        <span>推荐标题</span>
                        <strong>${escapeHtml(title)}</strong>
                      </button>`
                  )
                  .join("")}
              </div>
            </section>`
        )
        .join("")
    : `<div class="chat-placeholder">输入你想写的内容、目标客户、产品卖点和转化目标，AI 会在这里生成选题方向和标题。</div>`;
  return `
    <div class="creator-chat panel">
      <h3>和 AI 说明你想写什么</h3>
      <div class="chat-body">
        ${state.topicPrompt ? `<div class="chat-message user"><strong>用户</strong><p>${escapeHtml(state.topicPrompt)}</p></div>` : ""}
        ${state.isGeneratingTopics ? `<div class="chat-message assistant"><strong>AI</strong><p>正在生成选题方向和标题...</p></div>` : directionHtml}
      </div>
      <div class="chat-input-row">
        <input class="input" id="topicPromptInput" value="${escapeHtml(state.topicPrompt)}" placeholder="例如：写给本地餐饮老板，介绍暑期套餐如何提升到店复购" />
        <button class="accent-button" data-action="generate-topic-recommendations" ${state.isGeneratingTopics ? "disabled" : ""}>发送</button>
      </div>
    </div>`;
}

function renderCreatorArticle() {
  const blocks = state.articleBlocks || [];
  const title = state.selectedTitle || "请先在标题确认中选择标题";
  return `
    <div class="panel">
      <div class="panel-header">
        <h3>公众号文章创作</h3>
        <span class="status ${blocks.length ? "ok" : "info"}">${blocks.length ? "草稿已自动保存" : "等待生成文章"}</span>
      </div>
      <div class="field">
        <label>已确认标题</label>
        <input class="input" id="articleTitleInput" value="${escapeHtml(title)}" />
      </div>
      <div class="article-sections">
        ${
          state.isGeneratingArticle
            ? `<div class="empty-state">AI 正在生成文章段落...</div>`
            : blocks.length
              ? blocks.map((block, index) => articleSegment(`${index + 1}. ${block.label || block.type || "正文段落"}`, block.content || "", index)).join("")
              : `<div class="empty-state">确认标题后，系统会自动生成公众号文章段落。</div>`
        }
      </div>
      <div class="button-row block-gap">
        <button class="secondary-button" data-creator-step="title">上一步</button>
        <button class="accent-button wide" data-action="article-to-images" ${blocks.length ? "" : "disabled"}>文章完成，进入生成图片</button>
      </div>
    </div>`;
}

function articleSegment(title, content, index) {
  return `
    <div class="article-segment">
      <label>${title}</label>
      <textarea class="textarea" data-article-index="${index}">${escapeHtml(content)}</textarea>
    </div>`;
}

function renderCreatorImage() {
  return `
    <div class="panel">
      <div class="panel-header">
        <div>
          <h3>图片生成</h3>
          <p class="muted">基于已确认标题和文章内容统一生成封面图和正文配图。</p>
        </div>
      </div>
      ${imageGallery("公众号封面图", "cover")}
      ${imageGallery("正文配图", "inline")}
      <div class="button-row block-gap">
        <button class="secondary-button" data-creator-step="article">上一步</button>
        <button class="accent-button wide" data-creator-next="layout">图片完成，进入文章排版</button>
      </div>
    </div>`;
}

function imageGallery(title, type) {
  const assets = state.mediaAssets.filter((asset) => asset.asset_type === type);
  const isLoading = state.loadingImageType === type || state.loadingImageType === "all";
  return `
    <div class="image-gallery">
      <div class="panel-header">
        <h3>${title}</h3>
        <button class="secondary-button" data-action="regenerate-image" data-type="${type}" ${state.isGeneratingImages ? "disabled" : ""}>继续生图</button>
      </div>
      <div class="image-options">
        ${assets.map((asset, index) => imageOption(asset, index)).join("")}
        ${isLoading ? imageLoadingOption() : ""}
        ${!assets.length && !isLoading ? `<div class="empty-state">暂无${title}，进入本步骤后会自动生成。</div>` : ""}
      </div>
    </div>`;
}

function imageOption(asset, index) {
  const selected = Number(asset.selected) === 1;
  const type = asset.asset_type;
  return `
    <button class="image-option ${selected ? "active" : ""}" data-action="select-image" data-type="${type}" data-asset-id="${asset.id}">
      <img src="${fullAssetUrl(asset.url)}" alt="${type === "cover" ? "封面图" : "正文配图"} ${index + 1}" />
      ${selected ? '<span class="check-mark">✓</span>' : ""}
    </button>`;
}

function imageLoadingOption() {
  return `
    <div class="image-option loading-option">
      <div class="image-loading-card">
        <div class="image-loading-icon">▧</div>
        <strong>图片正在生成中</strong>
        <span>请稍候，精彩内容即将呈现</span>
        <div class="image-loading-spinner"></div>
      </div>
    </div>`;
}

function renderCreatorLayout() {
  const title = state.selectedTitle || "未确认标题";
  const blocks = state.articleBlocks || [];
  const cover = selectedAsset("cover");
  return `
    <div class="layout-workspace">
      <div class="panel">
        <div class="panel-header"><h3>编辑排版</h3></div>
        <div class="layout-editor">
          ${layoutBlock("标题", `<input class="input" data-layout-section="title" value="${escapeHtml(title)}" />`)}
          ${layoutBlock("公众号封面图", selectedImageRow(cover, "封面图"))}
          ${
            blocks.length
              ? blocks.map((block, index) => paragraphLayoutBlock(block, index)).join("")
              : `<div class="empty-state">暂无文章段落，请先生成文章。</div>`
          }
        </div>
        <div class="button-row block-gap">
          <button class="secondary-button" data-creator-step="image">上一步</button>
          <button class="accent-button wide" data-action="save-layout-and-publish">发布</button>
        </div>
        ${publishPanel()}
      </div>
      <div class="panel">
        <div class="panel-header"><h3>排版预览</h3></div>
        <div class="wechat-preview">
          ${cover ? `<img class="preview-cover-img" src="${fullAssetUrl(cover.url)}" alt="公众号封面图" />` : `<div class="preview-cover"><div class="preview-cover-title">未选择封面图</div></div>`}
          <h2>${escapeHtml(title)}</h2>
          ${blocks.map((block, index) => paragraphPreviewBlock(block, index)).join("")}
        </div>
      </div>
    </div>`;
}

function paragraphLayoutBlock(block, index) {
  const label = block.label || block.block_type || block.type || `正文 ${index + 1}`;
  return layoutBlock(
    `${index + 1}. ${escapeHtml(label)}`,
    `<textarea class="textarea" data-layout-text-index="${index}">${escapeHtml(block.content || "")}</textarea>
    ${paragraphImagePicker(index)}`
  );
}

function paragraphImagePicker(index) {
  const assets = inlineAssets();
  const selected = layoutImageForBlock(index);
  const options = [
    `<option value="">不插入图片</option>`,
    ...assets.map(
      (asset, assetIndex) =>
        `<option value="${asset.id}" ${selected?.id === asset.id ? "selected" : ""}>正文配图 ${assetIndex + 1}</option>`
    ),
  ].join("");
  return `
    <div class="paragraph-image-picker">
      <label>段落配图</label>
      <select class="select" data-layout-image-index="${index}">${options}</select>
      ${selectedImageRow(selected, "正文配图")}
    </div>`;
}

function paragraphPreviewBlock(block, index) {
  const image = layoutImageForBlock(index);
  const text = block.content || "";
  return `
    ${index === 0 && text ? `<blockquote>${escapeHtml(text)}</blockquote>` : text ? `<p>${escapeHtml(text)}</p>` : ""}
    ${image ? `<img class="preview-inline-img" src="${fullAssetUrl(image.url)}" alt="正文配图 ${index + 1}" />` : ""}`;
}

function selectedImageRow(asset, label) {
  if (!asset) return `<div class="selected-image-row"><span class="mock-thumb">${label}</span><span>暂未选择${label}</span></div>`;
  return `<div class="selected-image-row"><img src="${fullAssetUrl(asset.url)}" alt="${label}" /><span>已选择${label}</span></div>`;
}

function layoutBlock(title, body) {
  return `
    <div class="layout-block">
      <div class="layout-block-head">
        <label>${title}</label>
        <div class="row-actions">
          <button class="icon-button" title="上移">▲</button>
          <button class="icon-button" title="下移">▼</button>
        </div>
      </div>
      ${body}
    </div>`;
}

function publishPanel() {
  const accountOptions = accounts
    .filter((account) => account.platform === "公众号")
    .map((account) => `<option>${escapeHtml(account.name)}</option>`)
    .join("");
  return `
    <div class="publish-panel ${state.publishOpen ? "show" : ""}" id="publishPanel">
      <div class="form-grid two">
        <div class="field">
          <label>选择平台</label>
          <select class="select" id="publishPlatform"><option>微信公众号</option></select>
        </div>
        <div class="field">
          <label>选择账号</label>
          <select class="select" id="publishAccount">${accountOptions}</select>
        </div>
      </div>
      <div class="button-row block-gap">
        <button class="primary-button" data-action="publish-success">确认发布</button>
        <button class="secondary-button" data-action="toggle-publish">取消</button>
      </div>
    </div>`;
}

function applyProject(project = {}) {
  if (!project) return;
  state.currentProjectId = project.id || state.currentProjectId;
  state.selectedTitle = project.title || state.selectedTitle;
  state.titleDirection = project.title_direction || state.titleDirection;
  state.articleBlocks = project.article_blocks || state.articleBlocks;
  state.mediaAssets = project.media_assets || state.mediaAssets;
  state.layoutBlocks = project.layout_blocks || state.layoutBlocks;
  if (project.layout_blocks?.length) {
    state.layoutImageSelections = {};
    project.layout_blocks
      .filter((block) => block.section_type !== "title")
      .forEach((block, index) => {
        if (block.media_asset_id) state.layoutImageSelections[index] = block.media_asset_id;
      });
  }
  state.creatorStep = creatorFrontendStep(project.current_stage || state.creatorStep);
}

async function saveCreatorStage(step = state.creatorStep) {
  if (!state.currentProjectId) return;
  const data = await apiRequest(`/api/content-projects/${state.currentProjectId}/stage`, {
    method: "PATCH",
    body: JSON.stringify({ stage: creatorBackendStage(step) }),
  });
  applyProject(data.project);
  state.articleBlocks = data.article_blocks || state.articleBlocks;
  state.mediaAssets = data.media_assets || state.mediaAssets;
  state.layoutBlocks = data.layout_blocks || state.layoutBlocks;
}

function collectArticleBlocks() {
  const fields = [...document.querySelectorAll("[data-article-index]")];
  if (!fields.length) return state.articleBlocks;
  return fields.map((field, index) => {
    return {
      block_type: index === 0 ? "intro" : "body",
      label: index === 0 ? "引言" : "正文",
      content: field.value,
      sort_order: index + 1,
    };
  });
}

async function saveArticleBlocks() {
  if (!state.currentProjectId || !state.articleBlocks.length) return;
  const data = await apiRequest(`/api/content-projects/${state.currentProjectId}/article`, {
    method: "PATCH",
    body: JSON.stringify({ blocks: collectArticleBlocks() }),
  });
  applyProject(data.project);
  state.articleBlocks = data.blocks || state.articleBlocks;
}

async function generateTopicRecommendations() {
  const input = document.querySelector("#topicPromptInput");
  const prompt = (input?.value || "").trim();
  if (!prompt) {
    window.alert("请先输入想写的内容");
    return;
  }
  state.topicPrompt = prompt;
  state.isGeneratingTopics = true;
  render();
  try {
    const data = await apiRequest("/api/topic-recommendations", {
      method: "POST",
      body: JSON.stringify({ user_prompt: prompt }),
    });
    state.topicDirections = data.directions || [];
  } catch (error) {
    showCreatorError(error);
  } finally {
    state.isGeneratingTopics = false;
    render();
  }
}

async function confirmTopicTitle(target) {
  const title = target.dataset.title || "";
  const direction = target.dataset.direction || "";
  if (!title) return;
  state.selectedTitle = title;
  state.titleDirection = direction;
  state.creatorStep = "article";
  state.isGeneratingArticle = true;
  render();
  try {
    const project = await apiRequest("/api/content-projects", {
      method: "POST",
      body: JSON.stringify({ title, title_direction: direction, platform: "微信公众号" }),
    });
    applyProject(project);
    state.creatorStep = "article";
    const article = await apiRequest(`/api/content-projects/${state.currentProjectId}/generate-article`, {
      method: "POST",
    });
    applyProject(article.project);
    state.creatorStep = "article";
    state.articleBlocks = article.blocks || article.project?.article_blocks || [];
  } catch (error) {
    state.creatorStep = "title";
    showCreatorError(error);
  } finally {
    state.isGeneratingArticle = false;
    render();
  }
}

async function enterImageStep() {
  if (!state.currentProjectId) {
    window.alert("请先确认标题");
    return;
  }
  state.creatorStep = "image";
  state.publishOpen = false;
  try {
    await saveArticleBlocks();
    state.creatorStep = "image";
    if (!state.mediaAssets.length) {
      state.isGeneratingImages = true;
      state.loadingImageType = "all";
      render();
      const data = await apiRequest(`/api/content-projects/${state.currentProjectId}/generate-images`, {
        method: "POST",
      });
      applyProject(data.project);
      state.creatorStep = "image";
      state.mediaAssets = data.assets || state.mediaAssets;
    } else {
      await saveCreatorStage("image");
      state.creatorStep = "image";
    }
  } catch (error) {
    showCreatorError(error);
  } finally {
    state.isGeneratingImages = false;
    state.loadingImageType = "";
    render();
  }
}

async function regenerateImage(type) {
  if (!state.currentProjectId) {
    window.alert("请先确认标题");
    return;
  }
  state.creatorStep = "image";
  state.isGeneratingImages = true;
  state.loadingImageType = type;
  render();
  try {
    const data = await apiRequest(`/api/content-projects/${state.currentProjectId}/generate-images`, {
      method: "POST",
      body: JSON.stringify({ asset_type: type }),
    });
    applyProject(data.project);
    state.creatorStep = "image";
    state.mediaAssets = data.assets || state.mediaAssets;
  } catch (error) {
    showCreatorError(error);
  } finally {
    state.isGeneratingImages = false;
    state.loadingImageType = "";
    render();
  }
}

async function selectImage(assetId, type) {
  const id = Number(assetId);
  if (!id || !state.currentProjectId) return;
  state.mediaAssets = state.mediaAssets.map((asset) => {
    if (type === "cover" && asset.asset_type === "cover") {
      return { ...asset, selected: asset.id === id ? 1 : 0 };
    }
    if (asset.id === id) {
      return { ...asset, selected: Number(asset.selected) === 1 ? 0 : 1 };
    }
    return asset;
  });
  render();
  try {
    const selectedIds = state.mediaAssets.filter((asset) => Number(asset.selected) === 1).map((asset) => asset.id);
    const data = await apiRequest(`/api/content-projects/${state.currentProjectId}/media-selection`, {
      method: "PATCH",
      body: JSON.stringify({ selected_asset_ids: selectedIds }),
    });
    applyProject(data.project);
    state.creatorStep = "image";
    state.mediaAssets = data.assets || state.mediaAssets;
    render();
  } catch (error) {
    showCreatorError(error);
  }
}

function collectLayoutBlocks() {
  const cover = selectedAsset("cover");
  const title = document.querySelector('[data-layout-section="title"]')?.value || state.selectedTitle;
  const textFields = [...document.querySelectorAll("[data-layout-text-index]")];
  state.selectedTitle = title;
  state.articleBlocks = textFields.map((field, index) => ({
    ...(state.articleBlocks[index] || {}),
    type: index === 0 ? "intro" : "body",
    label: index === 0 ? "引言" : "正文",
    content: field.value,
  }));
  const paragraphBlocks = textFields.map((field, index) => {
    const imageId = document.querySelector(`[data-layout-image-index="${index}"]`)?.value || "";
    if (imageId) state.layoutImageSelections[index] = Number(imageId);
    else delete state.layoutImageSelections[index];
    return {
      section_type: index === 0 ? "intro" : "body",
      content: field.value,
      media_asset_id: imageId ? Number(imageId) : null,
      sort_order: index + 2,
    };
  });
  return [
    { section_type: "title", content: title, media_asset_id: cover?.id || null, sort_order: 1 },
    ...paragraphBlocks,
  ];
}

async function saveLayout() {
  if (!state.currentProjectId) {
    window.alert("请先完成内容创作");
    return;
  }
  const data = await apiRequest(`/api/content-projects/${state.currentProjectId}/layout`, {
    method: "PATCH",
    body: JSON.stringify({ blocks: collectLayoutBlocks() }),
  });
  applyProject(data.project);
  state.creatorStep = "layout";
  state.layoutBlocks = data.layout_blocks || state.layoutBlocks;
}

async function openPublishConfig() {
  try {
    await saveLayout();
    state.publishOpen = true;
    render();
  } catch (error) {
    showCreatorError(error);
  }
}

async function publishPackage() {
  const platform = document.querySelector("#publishPlatform")?.value || "微信公众号";
  const accountName = document.querySelector("#publishAccount")?.value || "AI记账助手";
  try {
    await saveLayout();
    const data = await apiRequest(`/api/content-projects/${state.currentProjectId}/publish-package`, {
      method: "POST",
      body: JSON.stringify({ platform, account_name: accountName }),
    });
    state.publishOpen = false;
    drawerEyebrow.textContent = "发布结果";
    drawerTitle.textContent = "模拟发布成功";
    drawerBody.innerHTML = `
      <div class="detail-grid">
        ${detail("平台", escapeHtml(platform))}
        ${detail("账号", escapeHtml(accountName))}
        ${detail("发布状态", "已生成待发布包")}
        ${detail("发布包 ID", escapeHtml(data.package?.id || ""))}
        ${detail("后续动作", "等待接入微信公众号发布接口")}
      </div>`;
    drawer.classList.remove("center-modal");
    drawer.classList.add("show");
    drawer.setAttribute("aria-hidden", "false");
    render();
  } catch (error) {
    showCreatorError(error);
  }
}

function formatDraftTime(value = "") {
  if (!value) return "";
  return value.replace("T", " ").slice(0, 16);
}

function draftCountByRestoreStage(stage) {
  return state.drafts.filter((draft) => creatorFrontendStep(draft.restore_stage) === stage).length;
}

async function loadDrafts() {
  state.isLoadingDrafts = true;
  render();
  try {
    const params = new URLSearchParams();
    if (state.draftStartDate) params.set("start_date", state.draftStartDate);
    if (state.draftEndDate) params.set("end_date", state.draftEndDate);
    const query = params.toString();
    const data = await apiRequest(`/api/drafts${query ? `?${query}` : ""}`);
    state.drafts = data.drafts || [];
  } catch (error) {
    showCreatorError(error);
  } finally {
    state.isLoadingDrafts = false;
    render();
  }
}

async function continueDraft(projectId, restoreStage) {
  try {
    const project = await apiRequest(`/api/content-projects/${projectId}`);
    applyProject(project);
    state.currentView = "creator";
    state.creatorStep = creatorFrontendStep(restoreStage || project.current_stage || "title");
    state.publishOpen = false;
    render();
  } catch (error) {
    showCreatorError(error);
  }
}

async function deleteDraft(draftId, title) {
  const ok = window.confirm(`确定删除草稿「${title}」吗？删除后草稿箱不再显示该记录。`);
  if (!ok) return;
  try {
    await apiRequest(`/api/drafts/${draftId}`, { method: "DELETE" });
    state.drafts = state.drafts.filter((draft) => Number(draft.id) !== Number(draftId));
    render();
  } catch (error) {
    showCreatorError(error);
  }
}

function scheduleCreatorAutosave(kind) {
  window.clearTimeout(autosaveTimer);
  autosaveTimer = window.setTimeout(async () => {
    try {
      if (kind === "article") await saveArticleBlocks();
      if (kind === "layout") await saveLayout();
    } catch (error) {
      showCreatorError(error);
    }
  }, 900);
}

async function loadTasks() {
  state.isLoadingTasks = true;
  render();
  try {
    const params = new URLSearchParams();
    if (state.taskStatusFilter) params.set("status", state.taskStatusFilter);
    if (state.taskPlatformFilter) params.set("platform", state.taskPlatformFilter);
    if (state.taskSearch) params.set("q", state.taskSearch);
    const query = params.toString();
    const data = await apiRequest(`/api/tasks${query ? `?${query}` : ""}`);
    state.tasks = data.tasks || [];
  } catch (error) {
    showCreatorError(error);
  } finally {
    state.isLoadingTasks = false;
    render();
  }
}

function accountOptions(selected = "") {
  return accounts
    .map((account) => `<option ${account.name === selected ? "selected" : ""}>${escapeHtml(account.name)}</option>`)
    .join("");
}

function taskTimeForInput(value = "", executionMode = "once") {
  const normalized = String(value).trim().replace(" ", "T");
  if (executionMode === "repeat") {
    return normalized.includes("T") ? normalized.slice(11, 16) : normalized.slice(0, 5);
  }
  return normalized.slice(0, 16);
}

function taskTimeForApi(value = "") {
  const normalized = String(value).trim();
  return normalized.includes("T") ? normalized.replace("T", " ") : normalized;
}

function taskFormHtml(task = null) {
  const isEdit = Boolean(task);
  const executionMode = task?.execution_mode || "once";
  const repeatRule = task?.repeat_rule || "daily";
  const weeklyDay = task?.weekly_day ?? "1";
  return `
    <div class="form-grid">
      <div class="field">
        <label>配置平台</label>
        <select class="select" data-task-form="platform">
          ${["公众号", "小红书"].map((platform) => `<option ${task?.platform === platform ? "selected" : ""}>${platform}</option>`).join("")}
        </select>
      </div>
      <div class="field">
        <label>配置账号</label>
        <select class="select" data-task-form="account_name">${accountOptions(task?.account_name || "")}</select>
      </div>
      <div class="field">
        <label>配置主题</label>
        <input class="input" data-task-form="topic" value="${escapeHtml(task?.topic || "")}" placeholder="请输入任务主题" />
        <small class="muted">主题只是选题约束，具体标题会在任务执行时由 AI 生成建议并自动选择。</small>
      </div>
      <div class="field">
        <label>创作要求 <span class="optional-label">非必填</span></label>
        <textarea class="textarea" data-task-form="article_requirements" placeholder="例如：面向小微企业老板，加入老板熬夜对账的例子，突出 AI 记账能把 8 小时压缩到 5 分钟。">${escapeHtml(task?.article_requirements || "")}</textarea>
        <small class="muted">任务执行时，AI 会结合主题、账号信息和创作要求生成标题建议。</small>
      </div>
      <div class="field">
        <label>配置任务类型</label>
        <select class="select" data-task-form="task_type">
          <option ${task?.task_type === "微信公众号图文生成" ? "selected" : ""}>微信公众号图文生成</option>
          <option ${task?.task_type === "小红书图文生成（预留）" ? "selected" : ""}>小红书图文生成（预留）</option>
        </select>
        <small class="muted">当前仅微信公众号图文生成会真实执行，小红书为预留能力。</small>
      </div>
      <div class="field">
        <label>执行方式</label>
        <select class="select" data-task-form="execution_mode">
          <option value="once" ${executionMode === "once" ? "selected" : ""}>单次执行</option>
          <option value="repeat" ${executionMode === "repeat" ? "selected" : ""}>重复执行</option>
        </select>
      </div>
      <div class="field" data-task-schedule="repeat_rule">
        <label>重复规则</label>
        <select class="select" data-task-form="repeat_rule">
          <option value="daily" ${repeatRule === "daily" ? "selected" : ""}>每天</option>
          <option value="weekly" ${repeatRule === "weekly" ? "selected" : ""}>每周</option>
        </select>
      </div>
      <div class="field" data-task-schedule="weekly_day">
        <label>每周星期</label>
        <select class="select" data-task-form="weekly_day">
          ${["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            .map((label, index) => `<option value="${index}" ${Number(weeklyDay) === index ? "selected" : ""}>${label}</option>`)
            .join("")}
        </select>
      </div>
      <div class="field">
        <label>配置执行时间</label>
        <input class="input" type="${executionMode === "repeat" ? "time" : "datetime-local"}" data-task-form="execute_time" value="${escapeHtml(taskTimeForInput(task?.execute_time || "", executionMode))}" />
      </div>
      <div class="button-row">
        <button class="primary-button" data-action="${isEdit ? "update-task" : "create-task"}" ${isEdit ? `data-task-id="${task.id}"` : ""}>
          ${isEdit ? "保存修改并进入待执行" : "创建任务并进入待执行"}
        </button>
        <button class="secondary-button" data-action="close-drawer">取消</button>
      </div>
    </div>`;
}

function openTaskForm(task = null) {
  drawerEyebrow.textContent = "任务中心";
  drawerTitle.textContent = task ? "编辑任务" : "新增任务";
  drawerBody.innerHTML = taskFormHtml(task);
  syncTaskScheduleFields();
  drawer.classList.add("center-modal");
  drawer.classList.add("show");
  drawer.setAttribute("aria-hidden", "false");
}

function syncTaskScheduleFields() {
  const mode = drawerBody.querySelector('[data-task-form="execution_mode"]')?.value || "once";
  const rule = drawerBody.querySelector('[data-task-form="repeat_rule"]')?.value || "daily";
  const repeatField = drawerBody.querySelector('[data-task-schedule="repeat_rule"]');
  const weeklyField = drawerBody.querySelector('[data-task-schedule="weekly_day"]');
  const timeInput = drawerBody.querySelector('[data-task-form="execute_time"]');
  if (repeatField) repeatField.hidden = mode !== "repeat";
  if (weeklyField) weeklyField.hidden = mode !== "repeat" || rule !== "weekly";
  if (timeInput) {
    const currentValue = timeInput.value;
    timeInput.type = mode === "repeat" ? "time" : "datetime-local";
    if (mode === "repeat" && currentValue.includes("T")) {
      timeInput.value = currentValue.slice(11, 16);
    }
    if (mode !== "repeat" && /^\d{2}:\d{2}$/.test(currentValue)) {
      timeInput.value = "";
    }
  }
}

function readTaskPayload() {
  const getValue = (key) => drawerBody.querySelector(`[data-task-form="${key}"]`)?.value.trim() || "";
  return {
    platform: getValue("platform"),
    account_name: getValue("account_name"),
    topic: getValue("topic"),
    article_requirements: getValue("article_requirements"),
    execute_time: taskTimeForApi(getValue("execute_time")),
    task_type: getValue("task_type"),
    execution_mode: getValue("execution_mode"),
    repeat_rule: getValue("repeat_rule"),
    weekly_day: getValue("weekly_day") ? Number(getValue("weekly_day")) : null,
    status: "待执行",
  };
}

async function createTask() {
  const payload = readTaskPayload();
  if (!payload.account_name || !payload.topic || !payload.execute_time) {
    window.alert("请填写账号、主题并配置执行时间");
    return;
  }
  try {
    await apiRequest("/api/tasks", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    drawer.classList.remove("show", "center-modal");
    drawer.setAttribute("aria-hidden", "true");
    await loadTasks();
  } catch (error) {
    showCreatorError(error);
  }
}

async function updateTask(taskId) {
  const payload = readTaskPayload();
  if (!payload.account_name || !payload.topic || !payload.execute_time) {
    window.alert("请填写账号、主题并配置执行时间");
    return;
  }
  try {
    await apiRequest(`/api/tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    drawer.classList.remove("show", "center-modal");
    drawer.setAttribute("aria-hidden", "true");
    await loadTasks();
  } catch (error) {
    showCreatorError(error);
  }
}

async function retryFailedTasks() {
  try {
    await apiRequest("/api/tasks/retry", {
      method: "POST",
      body: JSON.stringify({ task_ids: state.selectedRetryTaskIds.map(Number) }),
    });
    state.retrySelectMode = false;
    state.selectedRetryTaskIds = [];
    await loadTasks();
  } catch (error) {
    showCreatorError(error);
  }
}

async function openTaskEditor(taskId) {
  try {
    const task = await apiRequest(`/api/tasks/${taskId}`);
    openTaskForm(task);
  } catch (error) {
    showCreatorError(error);
  }
}

async function openTaskDetail(taskId) {
  try {
    const task = await apiRequest(`/api/tasks/${taskId}`);
    const project = task.content_project || {};
    const packageInfo = task.publish_package || {};
    const runRows = (task.runs || [])
      .map(
        (run) => `
          <tr>
            <td>${escapeHtml(run.started_at)}</td>
            <td><span class="status ${statusClass(run.status)}">${escapeHtml(run.status)}</span></td>
            <td>${escapeHtml(run.failure_stage || "-")}</td>
            <td>${escapeHtml(run.failure_reason || "-")}</td>
          </tr>`
      );
    drawerEyebrow.textContent = "任务中心";
    drawerTitle.textContent = task.topic;
    drawerBody.innerHTML = `
      <div class="detail-grid">
        ${detail("平台", escapeHtml(task.platform))}
        ${detail("账号", escapeHtml(task.account_name))}
        ${detail("主题约束", escapeHtml(task.topic))}
        ${detail("选定标题", escapeHtml(task.selected_title || task.topic))}
        ${detail("任务类型", escapeHtml(task.task_type))}
        ${detail("执行时间", escapeHtml(task.execute_time))}
        ${detail("执行方式", task.execution_mode === "repeat" ? "重复执行" : "单次执行")}
        ${detail("重复规则", task.execution_mode === "repeat" ? (task.repeat_rule === "weekly" ? "每周" : "每天") : "不重复")}
        ${detail("状态", `<span class="status ${statusClass(task.status)}">${escapeHtml(task.status)}</span>`)}
        ${detail("失败阶段", escapeHtml(task.failure_stage || "-"))}
        ${detail("失败原因", escapeHtml(task.failure_reason || "-"))}
        ${detail("关联内容", project.id ? `内容项目 #${project.id}：${escapeHtml(project.title || "")}` : "暂无")}
        ${detail("待发布包", packageInfo.id ? `发布包 #${packageInfo.id}，等待真实公众号发布接口` : "暂无")}
      </div>
      <div class="block-gap">
        <h3>重试记录</h3>
        ${
          runRows.length
            ? table(["开始时间", "结果", "失败阶段", "失败原因"], runRows)
            : `<div class="empty-state">暂无执行记录</div>`
        }
      </div>`;
    drawer.classList.remove("center-modal");
    drawer.classList.add("show");
    drawer.setAttribute("aria-hidden", "false");
  } catch (error) {
    showCreatorError(error);
  }
}

async function deleteTask(taskId, topic) {
  const ok = window.confirm(`确定删除任务「${topic}」吗？删除后不会再自动执行。`);
  if (!ok) return;
  try {
    await apiRequest(`/api/tasks/${taskId}`, { method: "DELETE" });
    await loadTasks();
  } catch (error) {
    showCreatorError(error);
  }
}

function renderDrafts() {
  const rows = state.drafts.map(
    (draft) => `
      <tr>
        <td><strong>${escapeHtml(draft.title)}</strong></td>
        <td><span class="status info">${escapeHtml(draft.stage)}</span></td>
        <td>${escapeHtml(draft.platform)}</td>
        <td>${escapeHtml(formatDraftTime(draft.updated_at))}</td>
        <td>
          <button class="ghost-button" data-draft-project="${draft.project_id}" data-draft-restore="${escapeHtml(draft.restore_stage)}">继续创作</button>
          <button class="ghost-button danger" data-action="delete-draft" data-draft-id="${draft.id}" data-draft-title="${escapeHtml(draft.title)}">删除</button>
        </td>
      </tr>`
  );

  return `
    ${metricCards([
      { label: "全部草稿", value: String(state.drafts.length), note: "按最近更新时间排序" },
      { label: "标题确认", value: String(draftCountByRestoreStage("title")), note: "可恢复到标题确认" },
      { label: "生成文章", value: String(draftCountByRestoreStage("article")), note: "可继续编辑正文" },
      { label: "生成图片 / 排版", value: String(draftCountByRestoreStage("image") + draftCountByRestoreStage("layout")), note: "可继续选图或发布" },
    ])}
    <div class="panel block-gap">
      <div class="panel-header">
        <div>
          <h3>内容创作草稿</h3>
          <p class="muted">内容创作任一步骤完成后都会自动保存到草稿箱，可从对应步骤继续创作。</p>
        </div>
      </div>
      <div class="toolbar">
        <div class="filters">
          <input class="input" type="date" data-draft-filter="start" value="${escapeHtml(state.draftStartDate)}" />
          <input class="input" type="date" data-draft-filter="end" value="${escapeHtml(state.draftEndDate)}" />
          <button class="secondary-button" data-action="load-drafts">查询</button>
        </div>
      </div>
      ${
        state.isLoadingDrafts
          ? `<div class="empty-state">正在读取草稿...</div>`
          : rows.length
            ? table(["草稿标题", "当前步骤", "内容平台", "更新时间", "操作"], rows)
            : `<div class="empty-state">暂无真实草稿。完成标题确认、文章生成、图片生成或排版编辑后会自动出现在这里。</div>`
      }
    </div>`;
}

function renderTasks() {
  const rows = state.tasks.map(
    (task) => `
      <tr>
        ${
          state.retrySelectMode
            ? `<td><input type="checkbox" data-retry-task-id="${task.id}" ${state.selectedRetryTaskIds.includes(String(task.id)) ? "checked" : ""} ${task.status !== "失败" ? "disabled" : ""} /></td>`
            : ""
        }
        <td><span class="platform">${escapeHtml(task.platform)}</span></td>
        <td>${escapeHtml(task.account_name)}</td>
        <td>${escapeHtml(task.next_run_at || task.execute_time)}</td>
        <td>${escapeHtml(task.task_type)}</td>
        <td>
          <strong>${escapeHtml(task.selected_title || task.topic)}</strong>
          <small class="muted">主题：${escapeHtml(task.topic)}</small>
        </td>
        <td><span class="status ${statusClass(task.status)}">${escapeHtml(task.status)}</span></td>
        <td>
          <div class="row-actions">
            <button class="text-button" data-action="view-task" data-task-id="${task.id}">详情</button>
            <button class="text-button" data-action="edit-task" data-task-id="${task.id}">编辑</button>
            <button class="text-button danger" data-action="delete-task" data-task-id="${task.id}" data-task-topic="${escapeHtml(task.topic)}">删除</button>
          </div>
        </td>
      </tr>`
  );
  const headers = [
    ...(state.retrySelectMode ? ["选择"] : []),
    "平台",
    "账号",
    "下次执行时间",
    "任务类型",
    "主题",
    "状态",
    "操作",
  ];

  return `
    <div class="panel task-list-panel">
      <div class="toolbar">
        <div class="filters">
          <select class="select" data-task-filter="status">
            <option value="">全部状态</option>
            <option value="待执行" ${state.taskStatusFilter === "待执行" ? "selected" : ""}>待执行</option>
            <option value="执行中" ${state.taskStatusFilter === "执行中" ? "selected" : ""}>执行中</option>
            <option value="成功" ${state.taskStatusFilter === "成功" ? "selected" : ""}>成功</option>
            <option value="失败" ${state.taskStatusFilter === "失败" ? "selected" : ""}>失败</option>
            <option value="已重试" ${state.taskStatusFilter === "已重试" ? "selected" : ""}>已重试</option>
          </select>
          <select class="select" data-task-filter="platform">
            <option value="">全部平台</option>
            <option value="公众号" ${state.taskPlatformFilter === "公众号" ? "selected" : ""}>公众号</option>
            <option value="小红书" ${state.taskPlatformFilter === "小红书" ? "selected" : ""}>小红书</option>
            <option value="视频号" ${state.taskPlatformFilter === "视频号" ? "selected" : ""}>视频号</option>
            <option value="抖音" ${state.taskPlatformFilter === "抖音" ? "selected" : ""}>抖音</option>
          </select>
          <input class="input" data-task-filter="search" value="${escapeHtml(state.taskSearch)}" placeholder="搜索主题或账号" />
        </div>
        <div class="row-actions">
          ${
            state.retrySelectMode
              ? `
                <button class="primary-button" data-action="confirm-batch-retry" ${state.selectedRetryTaskIds.length ? "" : "disabled"}>确认重试</button>
                <button class="secondary-button" data-action="cancel-batch-retry">取消</button>`
              : `<button class="secondary-button" data-action="batch-retry-tasks">批量重试</button>`
          }
          <button class="primary-button" data-action="open-task-form">新增任务</button>
        </div>
      </div>
      ${
        state.retrySelectMode
          ? `<div class="notice">请选择需要立即重新执行的失败任务。未选择的失败任务不会变化。</div>`
          : ""
      }
      ${
        state.isLoadingTasks
          ? `<div class="empty-state">正在读取任务...</div>`
          : rows.length
            ? table(headers, rows)
            : `<div class="empty-state">暂无任务，点击右上角“新增任务”创建。</div>`
      }
    </div>`;
}

function renderSettings() {
  return `
    <div class="grid cols-2">
      ${settingPanel("平台授权配置", [
        ["公众号", "已授权", "12 个", "10 分钟前"],
        ["小红书", "未授权", "0 个", "未检测"],
        ["视频号", "授权失效", "1 个", "30 分钟前"],
      ])}
      <div class="panel">
        <div class="panel-header"><h3>AI 配置</h3></div>
        <div class="detail-grid">
          ${detail("默认模型", "内容生成模型 V1")}
          ${detail("默认风格", "专业、科普、案例化")}
          ${detail("敏感词", "节税承诺、夸大收益、未经确认政策")}
          ${detail("规则生效", "自动生效，保留回滚记录")}
        </div>
      </div>
      <div class="panel">
        <div class="panel-header"><h3>团队成员</h3><button class="secondary-button">新增成员</button></div>
        ${table(["成员", "团队", "联系方式", "状态"], [
          row(["林晓", "内容运营组", "lin@example.com", `<span class="status ok">启用</span>`]),
          row(["周宁", "内容运营组", "zhou@example.com", `<span class="status ok">启用</span>`]),
          row(["陈沐", "增长组", "chen@example.com", `<span class="status warn">停用</span>`]),
        ])}
      </div>
      <div class="panel">
        <div class="panel-header"><h3>发布安全配置</h3></div>
        <div class="detail-grid">
          ${detail("发布前确认", "关闭")}
          ${detail("失败提醒", "开启")}
          ${detail("重试提醒", "开启")}
          ${detail("授权失效提醒", "开启")}
          ${detail("提醒方式", "站内通知")}
        </div>
      </div>
    </div>`;
}

function settingPanel(title, rowsData) {
  const rows = rowsData.map((items) =>
    row([
      `<span class="platform">${items[0]}</span>`,
      `<span class="status ${statusClass(items[1])}">${items[1]}</span>`,
      items[2],
      items[3],
      `<button class="ghost-button">检测</button>`,
    ])
  );
  return `
    <div class="panel">
      <div class="panel-header"><h3>${title}</h3><button class="secondary-button">去授权</button></div>
      ${table(["平台", "授权状态", "授权账号数", "最近检测", "操作"], rows)}
    </div>`;
}

function row(items) {
  return `<tr>${items.map((item) => `<td>${item}</td>`).join("")}</tr>`;
}

function detail(label, value) {
  return `<div class="detail-row"><span>${label}</span><strong>${value}</strong></div>`;
}

function render() {
  pageTitle.textContent = pageTitles[state.currentView];
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === state.currentView);
  });
  creatorSubnav.classList.toggle("show", state.currentView === "creator");
  document.querySelectorAll(".subnav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.creatorStep === state.creatorStep);
  });

  const views = {
    home: renderHome,
    accounts: renderAccounts,
    analytics: renderAnalytics,
    creator: renderCreator,
    drafts: renderDrafts,
    tasks: renderTasks,
    settings: renderSettings,
  };

  viewRoot.innerHTML = views[state.currentView]();
}

function openAccount(index) {
  const account = accounts[index];
  drawerEyebrow.textContent = `${account.platform}账号详情`;
  drawerTitle.textContent = account.name;
  drawerBody.innerHTML = `
    <div class="detail-grid">
      ${detail("平台", account.platform)}
      ${detail("账号名称", account.name)}
      ${detail("运营人", account.owner)}
      ${detail("账号标签", account.tag)}
      ${detail("授权状态", account.auth)}
      ${detail("监测状态", account.monitor)}
      ${detail("角色定位", account.role)}
      ${detail("面向用户", account.audience)}
      ${detail("内容风格", account.style)}
      ${detail("禁忌方向", account.taboo)}
      ${detail("AI 动态偏好", account.preference)}
    </div>`;
  drawer.classList.remove("center-modal");
  drawer.classList.add("show");
  drawer.setAttribute("aria-hidden", "false");
}

function openTask(index) {
  const task = tasks[index];
  drawerEyebrow.textContent = "任务详情";
  drawerTitle.textContent = task[4];
  drawerBody.innerHTML = `
    <div class="detail-grid">
      ${detail("平台", task[0])}
      ${detail("账号", task[1])}
      ${detail("执行时间", task[2])}
      ${detail("任务类型", task[3])}
      ${detail("任务状态", task[5])}
      ${detail("生成内容", "系统到点后自动调用图文生成能力")}
      ${detail("发布配置", "自动发布使用任务中已配置的平台和账号")}
      ${detail("失败原因", task[5] === "失败" ? "发布接口失败，可人工重试" : "无")}
    </div>`;
  drawer.classList.remove("center-modal");
  drawer.classList.add("show");
  drawer.setAttribute("aria-hidden", "false");
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;

  if (target.dataset.view) {
    state.currentView = target.dataset.view;
    render();
    if (state.currentView === "drafts") await loadDrafts();
    if (state.currentView === "tasks") await loadTasks();
    return;
  }

  if (target.dataset.creatorStep) {
    if (state.currentProjectId && state.creatorStep === "article") {
      try {
        await saveArticleBlocks();
      } catch (error) {
        showCreatorError(error);
      }
    }
    state.currentView = "creator";
    state.creatorStep = target.dataset.creatorStep;
    state.publishOpen = false;
    if (state.currentProjectId) {
      try {
        await saveCreatorStage(state.creatorStep);
        state.creatorStep = target.dataset.creatorStep;
      } catch (error) {
        showCreatorError(error);
      }
    }
    render();
    return;
  }

  if (target.dataset.creatorNext) {
    state.currentView = "creator";
    state.creatorStep = target.dataset.creatorNext;
    state.publishOpen = false;
    if (state.currentProjectId) {
      try {
        await saveCreatorStage(state.creatorStep);
        state.creatorStep = target.dataset.creatorNext;
      } catch (error) {
        showCreatorError(error);
      }
    }
    render();
    return;
  }

  if (target.dataset.draftProject) {
    await continueDraft(Number(target.dataset.draftProject), target.dataset.draftRestore);
    return;
  }

  if (target.dataset.viewLink) {
    state.currentView = target.dataset.viewLink;
    render();
    if (state.currentView === "drafts") await loadDrafts();
    if (state.currentView === "tasks") await loadTasks();
    return;
  }

  if (target.dataset.analyticsTab) {
    state.analyticsTab = target.dataset.analyticsTab;
    render();
    return;
  }

  if (target.dataset.account) {
    openAccount(Number(target.dataset.account));
    return;
  }

  if (target.dataset.action === "close-drawer") {
    drawer.classList.remove("show", "center-modal");
    drawer.setAttribute("aria-hidden", "true");
    return;
  }

  if (target.dataset.action === "load-drafts") {
    await loadDrafts();
    return;
  }

  if (target.dataset.action === "open-task-form") {
    openTaskForm();
    return;
  }

  if (target.dataset.action === "edit-task") {
    await openTaskEditor(Number(target.dataset.taskId));
    return;
  }

  if (target.dataset.action === "view-task") {
    await openTaskDetail(Number(target.dataset.taskId));
    return;
  }

  if (target.dataset.action === "delete-task") {
    await deleteTask(Number(target.dataset.taskId), target.dataset.taskTopic || "");
    return;
  }

  if (target.dataset.action === "create-task") {
    await createTask();
    return;
  }

  if (target.dataset.action === "update-task") {
    await updateTask(Number(target.dataset.taskId));
    return;
  }

  if (target.dataset.action === "batch-retry-tasks") {
    state.retrySelectMode = true;
    state.selectedRetryTaskIds = [];
    if (state.taskStatusFilter !== "失败") state.taskStatusFilter = "失败";
    await loadTasks();
    return;
  }

  if (target.dataset.action === "confirm-batch-retry") {
    await retryFailedTasks();
    return;
  }

  if (target.dataset.action === "cancel-batch-retry") {
    state.retrySelectMode = false;
    state.selectedRetryTaskIds = [];
    render();
    return;
  }

  if (target.dataset.action === "delete-draft") {
    await deleteDraft(Number(target.dataset.draftId), target.dataset.draftTitle || "");
    return;
  }

  if (target.dataset.action === "generate-topic-recommendations") {
    await generateTopicRecommendations();
    return;
  }

  if (target.dataset.action === "confirm-topic-title") {
    await confirmTopicTitle(target);
    return;
  }

  if (target.dataset.action === "article-to-images") {
    await enterImageStep();
    return;
  }

  if (target.dataset.action === "regenerate-image") {
    await regenerateImage(target.dataset.type);
    return;
  }

  if (target.dataset.action === "select-image") {
    await selectImage(target.dataset.assetId, target.dataset.type);
    return;
  }

  if (target.dataset.action === "save-layout-and-publish") {
    await openPublishConfig();
    return;
  }

  if (target.dataset.action === "toggle-publish") {
    state.publishOpen = !state.publishOpen;
    render();
    return;
  }

  if (target.dataset.action === "publish-success") {
    await publishPackage();
  }
});

document.addEventListener("input", (event) => {
  const articleField = event.target.closest("[data-article-index]");
  if (articleField) {
    const index = Number(articleField.dataset.articleIndex);
    if (state.articleBlocks[index]) {
      state.articleBlocks[index] = { ...state.articleBlocks[index], content: articleField.value };
    }
    scheduleCreatorAutosave("article");
    return;
  }

  const layoutField = event.target.closest("[data-layout-section]");
  if (layoutField && layoutField.dataset.layoutSection === "title") {
    state.selectedTitle = layoutField.value;
    scheduleCreatorAutosave("layout");
    return;
  }

  const layoutText = event.target.closest("[data-layout-text-index]");
  if (layoutText) {
    const index = Number(layoutText.dataset.layoutTextIndex);
    if (state.articleBlocks[index]) {
      state.articleBlocks[index] = { ...state.articleBlocks[index], content: layoutText.value };
    }
    scheduleCreatorAutosave("layout");
    return;
  }

  const taskSearch = event.target.closest('[data-task-filter="search"]');
  if (taskSearch) {
    state.taskSearch = taskSearch.value.trim();
    window.clearTimeout(taskFilterTimer);
    taskFilterTimer = window.setTimeout(() => {
      loadTasks();
    }, 500);
  }
});

document.addEventListener("change", (event) => {
  const draftFilter = event.target.closest("[data-draft-filter]");
  if (draftFilter) {
    if (draftFilter.dataset.draftFilter === "start") state.draftStartDate = draftFilter.value;
    if (draftFilter.dataset.draftFilter === "end") state.draftEndDate = draftFilter.value;
    return;
  }

  const taskFilter = event.target.closest("[data-task-filter]");
  if (taskFilter) {
    if (taskFilter.dataset.taskFilter === "status") state.taskStatusFilter = taskFilter.value;
    if (taskFilter.dataset.taskFilter === "platform") state.taskPlatformFilter = taskFilter.value;
    loadTasks();
    return;
  }

  const taskSchedule = event.target.closest('[data-task-form="execution_mode"], [data-task-form="repeat_rule"]');
  if (taskSchedule) {
    syncTaskScheduleFields();
    return;
  }

  const layoutImage = event.target.closest("[data-layout-image-index]");
  if (!layoutImage) return;
  const index = Number(layoutImage.dataset.layoutImageIndex);
  if (layoutImage.value) state.layoutImageSelections[index] = Number(layoutImage.value);
  else delete state.layoutImageSelections[index];
  render();
  scheduleCreatorAutosave("layout");
});

document.addEventListener("change", (event) => {
  const retryTask = event.target.closest("[data-retry-task-id]");
  if (!retryTask) return;
  const taskId = retryTask.dataset.retryTaskId;
  if (retryTask.checked) {
    state.selectedRetryTaskIds = [...new Set([...state.selectedRetryTaskIds, taskId])];
  } else {
    state.selectedRetryTaskIds = state.selectedRetryTaskIds.filter((id) => id !== taskId);
  }
  render();
});

drawer.addEventListener("click", (event) => {
  if (event.target === drawer) {
    drawer.classList.remove("show", "center-modal");
    drawer.setAttribute("aria-hidden", "true");
  }
});

render();
