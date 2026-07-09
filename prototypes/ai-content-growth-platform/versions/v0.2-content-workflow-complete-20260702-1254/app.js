const state = {
  currentView: "hotspots",
  selectedTitle: "为什么小微企业老板应该先用 AI 改造记账",
  titleDirection: "AI 焦虑 + 财务降本 + 小微企业老板",
  creationStage: "article",
  activeIndustry: "推荐",
  activeReviewPlatform: "全部平台",
  activeReviewType: "按账号查看",
  customIndustryCount: 0,
  drafts: [
    {
      title: "为什么小微企业老板应该先用 AI 改造记账",
      platform: "微信公众号",
      stage: "文章已完成，待生成图片",
      updatedAt: "2026-07-02 14:20",
      restoreStage: "images"
    },
    {
      title: "小微企业财务自动化，从这 5 个环节开始",
      platform: "微信公众号",
      stage: "标题已确认",
      updatedAt: "2026-07-01 18:10",
      restoreStage: "article"
    }
  ],
  tasks: [
    { name: "每日抓取行业热点", platform: "全平台", frequency: "每天 09:00", status: "预留" },
    { name: "每周生成选题池", platform: "微信公众号", frequency: "每周一 10:00", status: "预留" }
  ],
  accounts: [
    { platform: "微信公众号", name: "AI 记账工具公众号" },
    { platform: "小红书", name: "好财 AI 助手" },
    { platform: "视频号", name: "好财集团视频号" },
    { platform: "抖音", name: "好财 AI 记账" }
  ]
};

const hotspotData = {
  推荐: [
    ["AI 正在重塑小微企业财务效率", "企业服务 / 适合公众号文章 / 可引出 AI 记账自动化。", 92, 91, "低", "AI 焦虑 + 财务降本 + 小微企业老板"],
    ["暑期公共服务预约量增长", "政务 / 适合攻略型文章 / 可做流程提醒和避坑清单。", 89, 72, "中", "政务服务 + 流程攻略 + 家庭用户"],
    ["AI 工具进入中小企业日常经营", "科技 / 适合观点型文章 / 可引出老板的 AI 认知焦虑。", 84, 86, "低", "AI 工具 + 中小企业经营 + 老板认知"],
    ["票据和流水自动化成为老板降本新抓手", "财税 / 适合干货文章 / 可讲财务自动化路径。", 86, 88, "低", "票据流水 + 自动化 + 降本增效"],
    ["短视频平台加大本地生活扶持", "本地生活 / 适合多平台内容 / 可做商家经营选题。", 82, 76, "中", "本地生活 + 商家经营 + 内容获客"]
  ],
  政务: [
    ["城市便民服务上线 AI 咨询窗口", "政务 / 适合热点解读 / 可讲服务效率提升。", 88, 68, "中", "政务 AI + 便民服务 + 效率提升"],
    ["企业办事材料线上化比例提升", "政务 / 适合干货攻略 / 可讲企业办事流程变化。", 81, 70, "低", "企业办事 + 数字化 + 流程清单"],
    ["政务大厅延时服务引发讨论", "政务 / 适合民生观察 / 可讲办事体验。", 78, 62, "中", "政务服务 + 民生体验 + 效率"],
    ["基层数据填报减负成为新焦点", "政务 / 适合观点文章 / 可讲数字化治理。", 75, 60, "中", "基层减负 + 数字化 + 治理效率"],
    ["城市公共服务预约系统升级", "政务 / 适合攻略清单 / 可做预约流程说明。", 83, 69, "低", "预约系统 + 公共服务 + 流程提醒"]
  ],
  教育: [
    ["AI 助教进入职业培训课堂", "教育 / 适合趋势解读 / 可讲企业培训降本。", 83, 66, "中", "AI 助教 + 职业培训 + 企业降本"],
    ["暑期研学报名热度上升", "教育 / 适合攻略文章 / 可讲家长选择标准。", 86, 72, "低", "暑期研学 + 家长决策 + 攻略"],
    ["高校毕业生就业服务数字化", "教育 / 适合政策解读 / 可讲就业服务变化。", 80, 64, "中", "就业服务 + 数字化 + 青年人群"],
    ["企业内训开始使用 AI 课程生成", "教育 / 适合 B2B 文章 / 可讲培训效率。", 82, 74, "低", "企业内训 + AI 课程 + 成本效率"],
    ["家长关注 AI 学习工具边界", "教育 / 适合观点评论 / 可讲工具与陪伴。", 79, 61, "中", "AI 学习 + 家长焦虑 + 工具边界"]
  ],
  科技: [
    ["AI Agent 开始承担重复办公流程", "科技 / 适合观点评论 / 可引出财务自动化。", 90, 89, "低", "AI Agent + 重复办公 + 财务自动化"],
    ["国产大模型进入企业应用阶段", "科技 / 适合行业观察 / 可讲落地场景。", 88, 82, "低", "大模型 + 企业应用 + 场景落地"],
    ["AI 搜索改变信息获取方式", "科技 / 适合认知文章 / 可讲老板信息差。", 84, 75, "低", "AI 搜索 + 信息差 + 决策效率"],
    ["自动化办公工具成为中小企业标配", "科技 / 适合干货文章 / 可讲低成本工具栈。", 86, 83, "低", "自动化办公 + 中小企业 + 工具栈"],
    ["数据安全成为 AI 应用前置条件", "科技 / 适合风险科普 / 可讲企业合规。", 79, 70, "中", "AI 安全 + 企业数据 + 合规"]
  ],
  法律: [
    ["企业合同审核开始引入 AI 辅助", "法律 / 适合风险科普 / 可讲合规边界。", 76, 61, "中", "AI 合规 + 企业合同 + 风险控制"],
    ["劳动用工合规问题被频繁讨论", "法律 / 适合老板科普 / 可讲用工风险。", 82, 73, "中", "劳动合规 + 企业老板 + 风险提醒"],
    ["知识产权保护进入内容创作领域", "法律 / 适合版权科普 / 可讲爆款仿写边界。", 79, 69, "中", "版权保护 + 内容创作 + 仿写边界"],
    ["数据合规成为企业数字化门槛", "法律 / 适合 B2B 解读 / 可讲客户数据保护。", 77, 65, "中", "数据合规 + 企业数字化 + 风险"],
    ["短视频营销合规处罚案例增多", "法律 / 适合案例文章 / 可讲宣传边界。", 81, 70, "中", "短视频营销 + 合规处罚 + 案例"]
  ],
  医疗: [
    ["医疗机构探索 AI 导诊和随访", "医疗 / 适合趋势解读 / 注意合规表达。", 79, 55, "高", "AI 医疗 + 导诊随访 + 合规边界"],
    ["基层医疗服务数字化升级", "医疗 / 适合政策观察 / 可讲服务效率。", 78, 58, "中", "基层医疗 + 数字化 + 服务效率"],
    ["健康管理类内容收藏量上升", "医疗 / 适合科普文章 / 需避免诊疗建议。", 84, 62, "高", "健康管理 + 科普内容 + 合规"],
    ["医院预约和报告查询流程优化", "医疗 / 适合流程攻略 / 可讲用户体验。", 80, 60, "中", "医院预约 + 报告查询 + 流程优化"],
    ["AI 辅助影像筛查引发关注", "医疗 / 适合行业观察 / 需强调辅助属性。", 83, 57, "高", "AI 影像 + 辅助筛查 + 医疗边界"]
  ],
  新闻媒体: [
    ["AI 生成内容进入媒体采编流程", "新闻媒体 / 适合行业观察 / 可讲效率与审核。", 85, 63, "中", "AI 内容生产 + 采编效率 + 审核机制"],
    ["短视频新闻解释类内容增长", "新闻媒体 / 适合视频脚本 / 可讲解释型内容。", 82, 68, "低", "短视频新闻 + 解释型内容 + 转发"],
    ["地方媒体开始做城市服务账号", "新闻媒体 / 适合账号运营 / 可讲服务化转型。", 80, 66, "低", "地方媒体 + 城市服务 + 账号转型"],
    ["热点事实核查成为内容信任关键", "新闻媒体 / 适合观点文章 / 可讲可信内容。", 78, 64, "中", "事实核查 + 内容信任 + 热点"],
    ["媒体矩阵运营开始重视数据复盘", "新闻媒体 / 适合运营干货 / 可讲多平台指标。", 81, 72, "低", "媒体矩阵 + 数据复盘 + 多平台运营"]
  ],
  旅游: [
    ["暑期城市周边游搜索量上升", "旅游 / 适合攻略清单 / 可做本地消费转化。", 87, 74, "低", "暑期旅游 + 周边游 + 消费攻略"],
    ["亲子游目的地内容热度增长", "旅游 / 适合小红书笔记 / 可讲家庭场景。", 85, 76, "低", "亲子游 + 家庭用户 + 种草"],
    ["县域旅游因短视频再次出圈", "旅游 / 适合抖音脚本 / 可讲目的地故事。", 88, 79, "中", "县域旅游 + 短视频 + 出圈"],
    ["酒店民宿开始用 AI 做客服", "旅游 / 适合企业服务 / 可讲降本增效。", 80, 78, "低", "酒店民宿 + AI 客服 + 降本"],
    ["避暑目的地攻略收藏量上升", "旅游 / 适合攻略文章 / 可讲清单和路线。", 84, 73, "低", "避暑攻略 + 路线清单 + 收藏"]
  ]
};

Object.keys(hotspotData).forEach((industry) => {
  hotspotData[industry] = hotspotData[industry].map(([title, desc, heat, conversion, risk, direction]) => ({
    title,
    desc,
    heat,
    conversion,
    risk,
    direction
  }));
});

const aiTopicTemplates = [
  {
    title: "为什么小微企业老板应该先用 AI 改造记账",
    reason: "能把 AI 焦虑落到财务成本和经营效率。",
    direction: "AI 焦虑 + 财务降本 + 小微企业老板"
  },
  {
    title: "发票、流水、账本三件事，正在被 AI 重新做一遍",
    reason: "适合讲清楚自动化路径，降低理解门槛。",
    direction: "票据流水 + 自动化路径 + 老板理解成本"
  },
  {
    title: "小微企业财务自动化，从这 5 个环节开始",
    reason: "清单型文章更适合收藏和转发。",
    direction: "干货清单 + 财务自动化 + 私信进群"
  }
];

const reviewData = {
  全部平台: { views: "12,800", interactions: "936", shares: "214", leads: "46" },
  微信公众号: { views: "5,420", interactions: "318", shares: "126", leads: "32" },
  小红书: { views: "3,860", interactions: "412", shares: "45", leads: "9" },
  视频号: { views: "1,740", interactions: "96", shares: "18", leads: "3" },
  抖音: { views: "1,780", interactions: "110", shares: "25", leads: "2" }
};

function qs(selector, scope = document) {
  return scope.querySelector(selector);
}

function qsa(selector, scope = document) {
  return Array.from(scope.querySelectorAll(selector));
}

function showToast(message) {
  let toast = qs(".toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2200);
}

function showView(viewName) {
  state.currentView = viewName;
  qsa(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === viewName));
  qsa(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${viewName}`));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function fillTitlePanel(title, direction) {
  state.selectedTitle = title;
  state.titleDirection = direction;
  const titleInput = qs("#view-hotspots .detail-panel input");
  const directionInput = qsa("#view-hotspots .detail-panel input")[1];
  if (titleInput) titleInput.value = title;
  if (directionInput) directionInput.value = direction;
  const score = qs(".score-ring strong");
  if (score) score.textContent = title.includes("AI") ? "86" : "74";
}

function renderHotspots(industry = state.activeIndustry) {
  state.activeIndustry = industry;
  const topics = hotspotData[industry] || hotspotData.推荐;
  const list = qs("#view-hotspots .topic-list");
  if (!list) return;

  list.innerHTML = topics
    .map(
      (topic, index) => `
        <article class="topic-card ${index === 0 ? "selected" : ""}" data-title="${topic.title}" data-direction="${topic.direction}">
          <div class="rank">${index + 1}</div>
          <div class="topic-main">
            <h4>${topic.title}</h4>
            <p>${topic.desc}</p>
            <div class="meta-row">
              <span>热度 ${topic.heat}</span>
              <span>转化 ${topic.conversion}</span>
              <span>风险 ${topic.risk}</span>
            </div>
          </div>
          <button class="small-button" data-action="confirm-title">确认标题</button>
        </article>
      `
    )
    .join("");

  fillTitlePanel(topics[0].title, topics[0].direction);
}

function activateIndustryTab(industry) {
  qsa(".tabs .tab").forEach((item) => {
    item.classList.toggle("active", item.textContent.trim() === industry);
  });
  renderHotspots(industry);
}

function buildCustomTopics(industry, keywords, audience) {
  const keywordText = keywords || "行业热点、客户痛点、转化线索";
  const audienceText = audience || "目标客户";
  return [
    [`${industry}客户正在关注的 5 个新变化`, `${industry} / 适合热点解读 / 围绕${keywordText}做趋势判断。`, 86, 78, "低", `${industry} + ${keywordText} + ${audienceText}`],
    [`${industry}老板最容易忽略的增长机会`, `${industry} / 适合观点文章 / 适合引导读者自查。`, 84, 80, "低", `${industry} + 增长机会 + ${audienceText}`],
    [`${industry}服务效率提升，先从这 3 个环节开始`, `${industry} / 适合干货清单 / 可输出步骤和方法。`, 82, 76, "低", `${industry} + 效率提升 + 方法清单`],
    [`${industry}近期爆款内容都在讲什么`, `${industry} / 适合仿写拆解 / 可总结标题和结构。`, 88, 74, "中", `${industry} + 爆款拆解 + 内容仿写`],
    [`${industry}用户决策变慢，企业该怎么应对`, `${industry} / 适合转化文章 / 可连接产品或服务卖点。`, 80, 79, "中", `${industry} + 用户决策 + 转化切入`]
  ].map(([title, desc, heat, conversion, risk, direction]) => ({
    title,
    desc,
    heat,
    conversion,
    risk,
    direction
  }));
}

function openCustomIndustryModal() {
  openModal({
    title: "自定义行业",
    body: `
      <label>行业名称</label>
      <input id="modal-industry-name" value="企业服务" placeholder="例如：餐饮、财税、制造业" />
      <label>热点关键词</label>
      <input id="modal-industry-keywords" value="AI 降本、老板焦虑、自动化" placeholder="用逗号分隔关键词" />
      <label>目标读者</label>
      <input id="modal-industry-audience" value="小微企业老板" placeholder="例如：门店老板、财务负责人" />
      <label>行业说明</label>
      <textarea id="modal-industry-note" rows="4">用于让 AI 生成更贴近该行业的热点话题、标题方向和转化切入。</textarea>
    `,
    primaryText: "保存并生成热点",
    onConfirm: () => {
      const nameInput = qs("#modal-industry-name");
      const keywordInput = qs("#modal-industry-keywords");
      const audienceInput = qs("#modal-industry-audience");
      const industry = nameInput.value.trim() || `自定义行业${state.customIndustryCount + 1}`;
      hotspotData[industry] = buildCustomTopics(industry, keywordInput.value.trim(), audienceInput.value.trim());

      if (!qsa(".tabs .tab").some((tab) => tab.textContent.trim() === industry)) {
        const tab = document.createElement("button");
        tab.className = "tab";
        tab.textContent = industry;
        const customTab = qs("[data-action='custom-industry']");
        customTab.before(tab);
      }
      state.customIndustryCount += 1;
      activateIndustryTab(industry);
      showToast(`已生成${industry}的 5 个热点话题`);
    }
  });
}

function renderAiTopics(topics = aiTopicTemplates) {
  const panel = qs("#view-ai-topic .chat-layout > .panel:last-child");
  if (!panel) return;
  panel.innerHTML = `<h3>AI 推荐话题</h3>${topics
    .map(
      (topic, index) => `
        <div class="option-card ${index === 0 ? "selected" : ""}" data-title="${topic.title}" data-direction="${topic.direction}">
          <strong>${topic.title}</strong>
          <p>推荐理由：${topic.reason}</p>
          <button class="small-button" data-action="confirm-title">确认标题</button>
        </div>
      `
    )
    .join("")}`;
}

function confirmTitle(title, direction) {
  state.selectedTitle = title;
  state.titleDirection = direction;
  qsa("#view-creation input").forEach((input, index) => {
    if (index === 0) input.value = title;
  });
  setCreationStage("article");
  saveDraftSnapshot({
    title,
    platform: "微信公众号",
    stage: "标题已确认",
    restoreStage: "article"
  }, true);
  showView("creation");
  showToast("标题已确认，并自动保存到草稿箱");
}

function setCreationStage(stage) {
  state.creationStage = stage;
  const stageIndex = { article: 1, images: 2, layout: 3, publish: 4 }[stage] ?? 1;
  const segmentIndex = { article: 0, images: 1, layout: 2, publish: 2 }[stage] ?? 0;
  qsa(".workflow-strip .step").forEach((step, index) => {
    step.classList.toggle("done", index < stageIndex);
    step.classList.toggle("active", index === stageIndex);
  });
  qsa(".segmented button").forEach((button, index) => {
    button.classList.toggle("active", index === segmentIndex);
  });

  qsa("[data-stage-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.stagePanel === stage);
  });
}

function advanceCreationStage() {
  const next = { article: "images", images: "layout", layout: "publish", publish: "drafts" }[state.creationStage];
  saveCurrentDraft(true);
  if (next === "drafts") {
    showView("drafts");
    showToast("已保存到草稿箱，状态为待发布");
    return;
  }
  setCreationStage(next);
  showToast("已自动保存到草稿箱");
}

function getDraftLabel(stage = state.creationStage) {
  if (stage === "article") return { stage: "文章已完成，待生成图片", restoreStage: "images" };
  if (stage === "images") return { stage: "图片已完成，待排版", restoreStage: "layout" };
  if (stage === "layout") return { stage: "排版已完成，待发布", restoreStage: "publish" };
  return { stage: "待人工发布", restoreStage: "publish" };
}

function getTimestamp() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")} ${String(
    now.getHours()
  ).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
}

function renderDrafts(filterDate = "") {
  const tbody = qs("#view-drafts tbody");
  if (!tbody) return;
  const filtered = state.drafts
    .map((draft, originalIndex) => ({ draft, originalIndex }))
    .filter(({ draft }) => !filterDate || draft.updatedAt.startsWith(filterDate));
  tbody.innerHTML = filtered
    .map(
      ({ draft, originalIndex }) => `
        <tr>
          <td>${draft.title}</td>
          <td>${draft.platform}</td>
          <td><span class="status ${draft.stage.includes("标题") ? "muted" : ""}">${draft.stage}</span></td>
          <td>${draft.updatedAt}</td>
          <td><button class="small-button" data-action="restore-draft" data-index="${originalIndex}">${getRestoreText(draft.restoreStage)}</button></td>
        </tr>
      `
    )
    .join("");
}

function getRestoreText(stage) {
  if (stage === "images") return "回到图片生成步骤";
  if (stage === "layout") return "回到排版步骤";
  if (stage === "publish") return "回到发布步骤";
  return "继续写文章";
}

function saveCurrentDraft(silent = false) {
  const label = getDraftLabel();
  saveDraftSnapshot({
    title: state.selectedTitle,
    platform: "微信公众号",
    stage: label.stage,
    restoreStage: label.restoreStage
  }, silent);
}

function saveDraftSnapshot(snapshot, silent = false) {
  const existing = state.drafts.find((draft) => draft.title === snapshot.title && draft.platform === snapshot.platform);
  const nextDraft = {
    ...snapshot,
    updatedAt: getTimestamp()
  };
  if (existing) {
    Object.assign(existing, nextDraft);
    state.drafts = [existing, ...state.drafts.filter((draft) => draft !== existing)];
  } else {
    state.drafts.unshift(nextDraft);
  }
  renderDrafts();
  if (!silent) showToast("当前状态已保存到草稿箱");
}

function restoreDraft(index) {
  const draft = state.drafts[index];
  if (!draft) return;
  state.selectedTitle = draft.title;
  qsa("#view-creation input").forEach((input, inputIndex) => {
    if (inputIndex === 0) input.value = draft.title;
  });
  setCreationStage(draft.restoreStage);
  showView("creation");
  showToast(`已恢复草稿：${draft.stage}`);
}

function renderTasks() {
  const tbody = qs("#view-tasks tbody");
  if (!tbody) return;
  tbody.innerHTML = state.tasks
    .map(
      (task) => `
        <tr>
          <td>${task.name}</td>
          <td>${task.platform}</td>
          <td>${task.frequency}</td>
          <td><span class="status muted">${task.status}</span></td>
        </tr>
      `
    )
    .join("");
}

function openTaskModal() {
  openModal({
    title: "新建定时任务",
    body: `
      <label>任务名称</label>
      <input id="modal-task-name" value="每日生成热点话题推荐" />
      <label>平台</label>
      <select id="modal-task-platform">
        <option>全平台</option>
        <option>微信公众号</option>
        <option>小红书</option>
        <option>视频号</option>
        <option>抖音</option>
      </select>
      <label>频率</label>
      <input id="modal-task-frequency" value="每天 09:00" />
    `,
    primaryText: "保存任务",
    onConfirm: () => {
      state.tasks.unshift({
        name: qs("#modal-task-name").value,
        platform: qs("#modal-task-platform").value,
        frequency: qs("#modal-task-frequency").value,
        status: "预留"
      });
      renderTasks();
      showToast("任务已添加");
    }
  });
}

function renderReview() {
  const data = reviewData[state.activeReviewPlatform] || reviewData.全部平台;
  const values = qsa("#view-review .metric-card strong");
  [data.views, data.interactions, data.shares, data.leads].forEach((value, index) => {
    if (values[index]) values[index].textContent = value;
  });

  const title = qs("#view-review .layout-two .panel h3");
  if (title) title.textContent = state.activeReviewType === "按账号查看" ? "账号数据" : "作品数据";

  const tbody = qs("#view-review tbody");
  if (!tbody) return;
  if (state.activeReviewType === "按作品查看") {
    tbody.innerHTML = `
      <tr><td>为什么小微企业老板应该先用 AI 改造记账</td><td>微信公众号</td><td>阅读 2,180</td><td>18</td></tr>
      <tr><td>财务自动化从 5 个环节开始</td><td>小红书</td><td>浏览 3,120</td><td>7</td></tr>
    `;
  } else {
    tbody.innerHTML = `
      <tr><td>AI 记账工具公众号</td><td>微信公众号</td><td>8</td><td>32</td></tr>
      <tr><td>好财 AI 助手</td><td>小红书</td><td>12</td><td>9</td></tr>
    `;
  }
}

function renderAccounts() {
  const grid = qs("#view-accounts .account-grid");
  if (!grid) return;
  grid.innerHTML = state.accounts
    .map(
      (account, index) => `
        <article class="account-card">
          <strong>${account.platform}</strong>
          <p>${account.name}</p>
          <div class="account-actions">
            <button class="secondary-button" data-action="edit-account" data-index="${index}">编辑</button>
            <button class="ghost-button" data-action="delete-account" data-index="${index}">删除</button>
          </div>
        </article>
      `
    )
    .join("");
}

function openAccountModal(index = null) {
  const account = index === null ? { platform: "微信公众号", name: "" } : state.accounts[index];
  openModal({
    title: index === null ? "添加账号" : "编辑账号",
    body: `
      <label>平台</label>
      <select id="modal-account-platform">
        ${["微信公众号", "小红书", "视频号", "抖音"]
          .map((platform) => `<option ${platform === account.platform ? "selected" : ""}>${platform}</option>`)
          .join("")}
      </select>
      <label>账号名称</label>
      <input id="modal-account-name" value="${account.name}" placeholder="请输入账号名称" />
    `,
    primaryText: "保存账号",
    onConfirm: () => {
      const next = {
        platform: qs("#modal-account-platform").value,
        name: qs("#modal-account-name").value || "未命名账号"
      };
      if (index === null) state.accounts.push(next);
      else state.accounts[index] = next;
      renderAccounts();
      showToast(index === null ? "账号已添加" : "账号已更新");
    }
  });
}

function openModal({ title, body, primaryText, onConfirm }) {
  let modal = qs(".modal-backdrop");
  if (!modal) {
    modal = document.createElement("div");
    modal.className = "modal-backdrop";
    document.body.appendChild(modal);
  }
  modal.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <h3>${title}</h3>
        <button class="ghost-button" data-action="close-modal">关闭</button>
      </div>
      <div class="modal-body">${body}</div>
      <div class="modal-actions">
        <button class="secondary-button" data-action="close-modal">取消</button>
        <button class="primary-button" data-action="modal-confirm">${primaryText}</button>
      </div>
    </div>
  `;
  modal.classList.add("show");
  modal._onConfirm = onConfirm;
}

function closeModal() {
  const modal = qs(".modal-backdrop");
  if (modal) modal.classList.remove("show");
}

function wireEvents() {
  qsa(".nav-item").forEach((item) => {
    item.addEventListener("click", () => showView(item.dataset.view));
  });

  document.addEventListener("click", (event) => {
    const target = event.target;

    const tab = target.closest(".tabs .tab");
    if (tab) {
      if (tab.dataset.action === "custom-industry") {
        openCustomIndustryModal();
        return;
      }
      const industry = tab.textContent.trim();
      activateIndustryTab(industry);
      showToast(`已切换到${industry}热点`);
      return;
    }

    const card = target.closest(".topic-card, .option-card");
    if (card) {
      const group = card.classList.contains("topic-card") ? ".topic-card" : ".option-card";
      qsa(group, card.parentElement).forEach((item) => item.classList.remove("selected"));
      card.classList.add("selected");
      if (card.dataset.title) fillTitlePanel(card.dataset.title, card.dataset.direction || "");
    }

    if (target.matches("[data-action='confirm-title']")) {
      const sourceCard = target.closest(".topic-card, .option-card");
      confirmTitle(sourceCard.dataset.title, sourceCard.dataset.direction || "AI 选题推荐");
    }

    if (target.textContent.trim() === "确定标题并创作") {
      const inputs = qsa("#view-hotspots .detail-panel input");
      confirmTitle(inputs[0].value, inputs[1].value);
    }

    if (target.textContent.trim() === "AI 推荐选题" || target.textContent.trim() === "生成话题推荐") {
      renderAiTopics();
      showToast("AI 已生成 3 个话题推荐");
    }

    if (target.textContent.trim() === "发送") {
      const input = qs(".chat-input-row input");
      const text = input.value.trim();
      if (!text) return;
      const row = document.createElement("div");
      row.className = "chat-message user";
      row.innerHTML = `<strong>用户</strong><p>${text}</p>`;
      qs(".chat-input-row").before(row);
      const reply = document.createElement("div");
      reply.className = "chat-message assistant";
      reply.innerHTML = "<strong>AI</strong><p>收到，我会把这个补充纳入选题推荐，并优先给出可转化标题。</p>";
      qs(".chat-input-row").before(reply);
      input.value = "";
      renderAiTopics();
    }

    if (target.closest(".segmented") && target.tagName === "BUTTON") {
      const index = qsa(".segmented button").indexOf(target);
      setCreationStage(["article", "images", "layout"][index] || "article");
    }

    if (target.matches("[data-action='next-creation-step']")) {
      advanceCreationStage();
    }

    if (target.matches("[data-action='regenerate-cover']") || target.matches("[data-action='regenerate-inline']")) {
      saveCurrentDraft(true);
      showToast("图片已重新生成，并自动保存到草稿箱");
    }

    if (target.matches("[data-action='restore-draft']")) {
      restoreDraft(Number(target.dataset.index));
    }

    if (target.textContent.trim() === "查询") {
      const date = qs("#view-drafts input[type='date']").value;
      renderDrafts(date);
      showToast(date ? `已筛选 ${date} 的草稿` : "已显示全部草稿");
    }

    if (target.textContent.trim() === "新建定时任务") {
      openTaskModal();
    }

    if (target.textContent.trim() === "添加账号") {
      openAccountModal();
    }

    if (target.matches("[data-action='edit-account']")) {
      openAccountModal(Number(target.dataset.index));
    }

    if (target.matches("[data-action='delete-account']")) {
      const account = state.accounts[Number(target.dataset.index)];
      state.accounts.splice(Number(target.dataset.index), 1);
      renderAccounts();
      showToast(`已删除账号：${account.name}`);
    }

    if (target.matches("[data-action='close-modal']")) {
      closeModal();
    }

    if (target.matches("[data-action='modal-confirm']")) {
      const modal = qs(".modal-backdrop");
      if (modal?._onConfirm) modal._onConfirm();
      closeModal();
    }
  });

  qsa("#view-review select").forEach((select, index) => {
    select.addEventListener("change", () => {
      if (index === 0) state.activeReviewPlatform = select.value;
      if (index === 1) state.activeReviewType = select.value;
      renderReview();
      showToast("复盘数据已更新");
    });
  });
}

function init() {
  renderHotspots();
  renderAiTopics();
  renderDrafts();
  renderTasks();
  renderReview();
  renderAccounts();
  setCreationStage("article");
  wireEvents();
}

init();
