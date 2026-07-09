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

const aiTopicContent = [
  {
    direction: "选题方向一：效率颠覆（核心时间对比，直击痛点）",
    subtitle: "锚定「8小时→5分钟」「1小时→1分钟」的核心差异，用极致数字冲击唤醒老板对财务效率的焦虑。",
    titles: [
      "8小时→5分钟：老板亲自做账的一天，会计看了想辞职",
      "发票审核1小时→1分钟：这家公司行政小妹把财务部的活抢了",
      "从加班到晚10点，到下午3点下班：AI记账把会计变成了\"闲职\"",
      "1个人干6个人的活：人事小姐姐用AI记账帮公司省了30万",
      "以前月底3天对不完账，现在泡杯咖啡的功夫就搞定了",
      "8小时的账5分钟跑完：老板终于不用在财务报表面前装懂了",
      "从\"我不会做账\"到\"5分钟搞定全套账\"：行政人员的隐藏技能",
      "1分钟审完100张发票：比会计老花眼靠谱100倍的AI审核",
      "以前请会计花3天，现在老板自己5分钟：AI记账让专业变平民",
      "从填表填到手抽筋，到点三下就下班：AI记账的时间魔法"
    ]
  },
  {
    direction: "选题方向二：零门槛记账（老板/行政/人事都能用）",
    subtitle: "打破\"记账是会计专属\"的认知壁垒，强调产品零门槛，任何人都能上手。",
    titles: [
      "不懂借贷？没关系，老板自己5分钟也能把账做得明明白白",
      "会计证？不需要！行政小姐姐用AI记账，准确率比老会计还高",
      "从\"我看不懂账\"到\"我自己做账\"：一个老板的AI记账初体验",
      "零基础5分钟上手：人事主管把全公司账接了过来，工资涨了2000",
      "告别\"财务黑盒\"：老板不用会计，自己也能看清每一笔钱的去向",
      "以前做账要考初级会计证，现在只要会点鼠标：AI记账的零门槛革命",
      "行政兼做账、人事管工资、老板看报表：一人多岗的AI秘密武器",
      "90后老板亲身体验：我不会记账，但AI让我5分钟搞定了全套账",
      "从小白到账房先生：AI记账让每个人都是财务高手",
      "会计离职了？没事，行政用AI记账顶了3个月，老板反而更放心了"
    ]
  },
  {
    direction: "选题方向三：一人多岗赋能（行政/人事兼职做账，节省人力）",
    subtitle: "强调AI记账让非财务岗位人员也能承担财务工作，实现企业人力成本最优化。",
    titles: [
      "行政前台兼职做账，月薪8000干出了30000的价值：AI记账的秘密",
      "人事经理每天多花5分钟，公司省下了2个会计的工资：这笔账太划算",
      "一个前台+AI记账系统=一个财务部：创业公司的省钱神器",
      "人事兼财务、行政兼出纳：AI记账让小微企业不再需要专职会计",
      "从\"招不到会计\"到\"不招会计\"：AI记账让老板告别招人焦虑",
      "5分钟学会做账：你的前台可能比市场上的初级会计更靠谱",
      "一人多岗不是压榨，是AI赋能：员工效率翻倍的真相",
      "行政小妹5分钟做完全套账，老板：下个月给你涨薪！",
      "小公司不需要财务部？AI记账让一个人就是一个团队",
      "从\"招会计难\"到\"不招也行\"：AI记账解救了10000家小微企业"
    ]
  },
  {
    direction: "选题方向四：降本增效账本（算经济账，省钱角度）",
    subtitle: "把AI记账转化为看得见的省钱数字，让老板觉得不买就是亏钱。",
    titles: [
      "算笔账：请一个会计8万/年，AI记账3000/年，省下的钱够再招一个销售",
      "发票审核从1小时到1分钟：一年省下500小时，等于多上2个月班",
      "8小时变5分钟：会计省下的时间，能为公司多创造多少价值？",
      "从代账公司3000/月，到AI记账300/月：老板一年省下了3万多",
      "不用再请专职会计：一家10人公司用AI记账，人力成本直降40%",
      "月底不加班、年末不熬夜：AI记账省下的加班费够发3个月奖金",
      "1个行政+AI系统干6个人的活：小公司的人力成本就该这么控",
      "AI记账一年回本？不，一个月就回本了：老板们的真实账本",
      "把财务部砍掉一半人：AI记账后，这家公司利润提升了15%",
      "从花8万养会计，到花3千买AI：这笔账小学生都算得明白"
    ]
  },
  {
    direction: "选题方向五：老板自主管账（不再依赖会计/代账）",
    subtitle: "强调老板自己掌控财务数据，不再被会计\"绑架\"，实现财务透明化。",
    titles: [
      "以前会计说\"你不懂\"搪塞我，现在AI记账让我5分钟看懂全套账",
      "老板不做\"睁眼瞎\"：AI记账让每一分钱都透明可见",
      "从\"财务黑盒\"到\"一目了然\"：AI记账给了老板真正的财务掌控权",
      "会计请假，账就停摆？老板用AI记账，再也不用看人脸色",
      "不再被会计\"绑架\"：老板亲自做账，公司财务反而更安全",
      "代账公司把账做成\"天书\"，AI记账让我5分钟看懂盈亏",
      "从\"我不懂财务\"到\"我比会计还清楚\"：一个老板的AI记账觉醒",
      "老板离财务越近，离破产就越远：AI记账让老板不再当甩手掌柜",
      "不用会计翻译，老板直接看AI报表：这才是真正的财务自由",
      "把财务主动权拿回来：AI记账让老板不再被会计牵着鼻子走"
    ]
  },
  {
    direction: "选题方向六：发票闪电战（发票审核1小时→1分钟）",
    subtitle: "聚焦发票管理场景，用极致的时间对比展示AI在发票处理上的效率碾压。",
    titles: [
      "100张发票1分钟审完：AI审核比会计老花眼准100倍",
      "从\"发票堆成山\"到\"一秒一张\"：AI把发票审核变成了流水线",
      "以前审发票审到眼花，现在AI秒过：会计终于能准时下班了",
      "1分钟搞定发票审核：老板再也不用担心假发票、重复报了",
      "发票拍照即入账：AI记账把\"贴发票\"这个噩梦终结了",
      "从手工录入到AI识别：一张发票的旅程从1小时缩短到1秒",
      "月底发票对账3天？AI记账1小时搞定，准确率99.9%",
      "假发票、重复票、问题票：AI审核比人眼更尖，1分钟全部筛出",
      "以前贴发票贴到手软，现在拍照就完事：AI记账的发票革命",
      "发票审核提速60倍：1小时变1分钟，会计部改成了AI部"
    ]
  },
  {
    direction: "选题方向七：月末年末不加班（场景化时间解放）",
    subtitle: "聚焦月末结账、年末汇算等关键场景，用场景化叙事引发共鸣。",
    titles: [
      "以前月底加班3天，现在5分钟收工：AI记账让\"财务月\"成为历史",
      "年末汇算清缴不再通宵：AI记账让会计从\"ICU常客\"变正常人",
      "月底不再崩溃：AI记账5分钟搞定，会计终于能约会了",
      "从\"月底不见人\"到\"月底正常下班\"：AI记账改变了整个公司的节奏",
      "年末不再崩溃：AI记账自动生成报表，老板再也不用催会计了",
      "月底结账像打仗？AI记账让财务室从此没有\"月底\"",
      "以前月末加班到凌晨，现在下午3点提交报表：AI记账的时间魔法",
      "从\"月底别找我\"到\"随时看报表\"：AI记账让老板告别月底焦虑",
      "年末对账不头疼：AI记账一键生成，会计笑了，老板更笑",
      "月底5分钟、年末1小时：AI记账让财务工作变成\"日常\"而非\"战役\""
    ]
  },
  {
    direction: "选题方向八：告别代账外包（摆脱外包依赖）",
    subtitle: "揭露代账行业痛点，强调AI记账让老板不再依赖外部代账公司。",
    titles: [
      "3000元/月的代账公司，其实只做了AI 5分钟就能完成的事",
      "从\"账交给别人不放心\"到\"自己5分钟搞定\"：告别代账外包",
      "代账公司同时在服务200家，你的账能排第几？AI只服务你一家",
      "不再被代账公司\"绑架\"：AI记账让老板拿回财务自主权",
      "代账公司老板的自白：我用AI工具服务了比以前多5倍的客户",
      "从代账到AI自管：一个老板每年省下4万，还更放心",
      "代账公司说\"包过税审\"，真相是你的账根本没人仔细看",
      "告别\"账在别处、心里没底\"：AI记账让老板随时掌握财务动态",
      "AI记账vs代账公司：5分钟 vs 5天，你选哪个？",
      "把代账费省下来发工资：AI记账让小微企业不再被外包\"割韭菜\""
    ]
  },
  {
    direction: "选题方向九：财税零风险（老板视角防罚款）",
    subtitle: "从税务合规角度切入，用老板最怕的\"罚款\"制造焦虑，再给解药。",
    titles: [
      "税务稽查来了！手工做账的公司先被查：AI记账让你高枕无忧",
      "一张发票出错罚3万：AI审核1分钟，比会计100遍检查还安全",
      "从\"提心吊胆怕被查\"到\"AI自动合规\"：老板的财税风险归零之路",
      "金税四期下，还在手工做账等于\"裸奔\"：AI记账是你的防弹衣",
      "会计做的账vs AI做的账：税务局更相信哪一个？答案出人意料",
      "老板最怕的不是没生意，是税务罚款：AI记账的3道安全锁",
      "从被罚款10万到零风险：AI记账如何帮老板守住钱袋子",
      "假发票、错报税、漏缴税：AI记账比人更不容易犯错的3个原因",
      "老板不懂税没关系，AI懂：24小时不打烊的财务保镖",
      "年底查账季不用慌：AI记账的每一笔都有据可查，税务局也认"
    ]
  },
  {
    direction: "选题方向十：AI时代必备（借势AI热点，建立先进认知）",
    subtitle: "结合AI大趋势，把AI记账包装为企业数字化升级的\"入门标配\"。",
    titles: [
      "ChatGPT能写文案，AI能帮你记账：2024年老板必用的5个AI工具",
      "你的竞争对手已经在用AI记账了，你还在用手工做账？",
      "AI不是替代会计，是让老板、行政、人事都能5分钟搞定账",
      "从\"我不会用AI\"到\"离不开AI记账\"：一个传统老板的蜕变",
      "AI时代，不会用AI记账的企业，就像还在用算盘的账房先生",
      "大模型+财务：AI记账为什么比传统软件聪明100倍？",
      "未来3年，90%的企业会用AI记账：你是先行者还是掉队者？",
      "AI记账不只是快：它让老板第一次真正看懂了公司的账",
      "从Excel到AI：财务工具的进化，决定了企业的进化速度",
      "AI时代的企业标配：不会记账的老板，用AI也能5分钟搞定"
    ]
  }
];

function renderAiTopicChat() {
  const chatPanel = qs("#view-ai-topic .chat-panel");
  if (!chatPanel) return;
  const userPrompt = "我是一个10年的新媒体运营，现在运营公司的微信公众号。公司的产品是AI记账软件。我需要通过写微信公众号给我的用户观看。我的主要用户是企业老板。然后看完之后，希望老板能对我们的软件产生兴趣。AI记账软件核心功能是三步记账。之前财务需要花8个小时做一个企业的全套账。现在用我们的AI记账软件三步5分钟就能做完。包含其他的发票审核，然后之前可能花一个小时，现在一分钟就审核完毕。就类似的关于记账方面的各方面的AI功能，都能大大提高效率。给出选题的方向，每个选题方向给出10篇文章的标题出来。标题中应该多体现用AI之后的一个时间对比。第二，我们的AI记账软件不是给财务用的，是给所有的老板、行政、人事都可以用的。";
  const sectionsHtml = aiTopicContent.map((section, idx) => {
    const titlesHtml = section.titles.map((t, i) => `<li>${t}</li>`).join("");
    return `<div class="topic-direction"><h4>${section.direction}</h4><p class="topic-direction-sub">${section.subtitle}</p><ol start="${idx * 10 + 1}">${titlesHtml}</ol></div>`;
  }).join("");
  chatPanel.innerHTML = `
    <h3>和 AI 说明你想写什么</h3>
    <div class="chat-message user">
      <strong>用户</strong>
      <p>${userPrompt}</p>
    </div>
    <div class="chat-message assistant chat-long-content">
      <strong>AI</strong>
      <div class="topic-directions">${sectionsHtml}</div>
    </div>
    <div class="chat-input-row">
      <input value="继续补充：文章要更偏焦虑、干货还是案例？" />
      <button class="primary-button">发送</button>
    </div>
  `;
}

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

function renderAiTopics() {
  const panel = qs("#view-ai-topic .chat-layout > .panel:last-child");
  if (!panel) return;
  const sectionsHtml = aiTopicContent.map((section) => {
    const titlesHtml = section.titles.map((t) => {
      const safeTitle = t.replace(/"/g, '&quot;');
      const safeDir = section.direction.replace(/"/g, '&quot;');
      return `<div class="option-card" data-title="${safeTitle}" data-direction="${safeDir}"><strong>${t}</strong><button class="small-button" data-action="confirm-title">\u786e\u8ba4\u6807\u9898</button></div>`;
    }).join("");
    return `<div class="topic-section-group"><h4>${section.direction}</h4>${titlesHtml}</div>`;
  }).join("");
  panel.innerHTML = `<h3>AI \u63a8\u8350\u8bdd\u9898</h3><div class="topic-sections-scroll">${sectionsHtml}</div>`;
}

function confirmTitle(title, direction) {
  state.selectedTitle = title;
  state.titleDirection = direction;
  qsa("#view-creation input").forEach((input, index) => {
    if (index === 0) input.value = title;
  });
  saveDraftSnapshot({
    title,
    platform: "微信公众号",
    stage: "标题已确认",
    restoreStage: "article"
  }, true);

  // Show generating overlay
  showGeneratingOverlay(title);
}

function showGeneratingOverlay(title) {
  let overlay = qs(".generating-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "generating-overlay";
    document.body.appendChild(overlay);
  }
  overlay.innerHTML = `
    <div class="generating-card">
      <div class="generating-spinner"></div>
      <h3>内容生成中</h3>
      <p>AI 正在根据标题「${title}」生成完整文章内容，请稍候…</p>
      <div class="generating-progress">
        <div class="generating-progress-bar"></div>
      </div>
    </div>
  `;
  overlay.style.display = "flex";

  window.setTimeout(() => {
    overlay.style.display = "none";
    setCreationStage("article");
    showView("creation");
    showToast("文章已生成完成");
  }, 2800);
}

function setCreationStage(stage) {
  state.creationStage = stage;
  const stageIndex = { article: 1, images: 2, layout: 3, publish: 4 }[stage] ?? 1;
  qsa(".workflow-strip .step").forEach((step, index) => {
    step.classList.toggle("done", index < stageIndex);
    step.classList.toggle("active", index === stageIndex);
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

function goBackCreationStage() {
  if (state.creationStage === "article") {
    showView("hotspots");
    showToast("已返回热点话题推荐");
    return;
  }
  const prev = { images: "article", layout: "images", publish: "layout" }[state.creationStage];
  if (prev) {
    setCreationStage(prev);
    showToast("已返回上一步");
  }
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

function renderDrafts(startDate = "", endDate = "") {
  const tbody = qs("#view-drafts tbody");
  if (!tbody) return;
  const filtered = state.drafts
    .map((draft, originalIndex) => ({ draft, originalIndex }))
    .filter(({ draft }) => {
      if (!startDate && !endDate) return true;
      const date = draft.updatedAt.substring(0, 10);
      if (startDate && date < startDate) return false;
      if (endDate && date > endDate) return false;
      return true;
    });
  tbody.innerHTML = filtered
    .map(
      ({ draft, originalIndex }) => `
        <tr>
          <td>${draft.title}</td>
          <td>${draft.platform}</td>
          <td><span class="status ${draft.stage.includes("标题") ? "muted" : ""}">${draft.stage}</span></td>
          <td>${draft.updatedAt}</td>
          <td><button class="small-button" data-action="restore-draft" data-index="${originalIndex}">编辑</button></td>
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
      <label>频率类型</label>
      <select id="modal-freq-type">
        <option value="daily">每天</option>
        <option value="weekly">每周某天</option>
        <option value="range">选择日期</option>
      </select>
      <div id="freq-daily">
        <label>发布时间</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="modal-task-time-start" value="09:00" type="time" />
          <span style="color:#5d6e74">至</span>
          <input id="modal-task-time-end" value="09:00" type="time" />
        </div>
      </div>
      <div id="freq-weekly" style="display:none">
        <label>选择星期</label>
        <select id="modal-task-weekday">
          <option>周一</option><option>周二</option><option>周三</option>
          <option>周四</option><option>周五</option><option>周六</option><option>周日</option>
        </select>
        <label>发布时间</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="modal-task-weekly-time-start" value="10:00" type="time" />
          <span style="color:#5d6e74">至</span>
          <input id="modal-task-weekly-time-end" value="10:00" type="time" />
        </div>
      </div>
      <div id="freq-range" style="display:none">
        <label>开始日期</label>
        <input id="modal-task-range-start" value="2026-07-01" type="date" />
        <label>结束日期</label>
        <input id="modal-task-range-end" value="2026-07-31" type="date" />
        <label>发布时间</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="modal-task-range-time-start" value="09:00" type="time" />
          <span style="color:#5d6e74">至</span>
          <input id="modal-task-range-time-end" value="18:00" type="time" />
        </div>
      </div>
    `,
    primaryText: "保存任务",
    onConfirm: () => {
      const freqType = qs("#modal-freq-type").value;
      let frequency = "";
      if (freqType === "daily") {
        const start = qs("#modal-task-time-start").value;
        const end = qs("#modal-task-time-end").value;
        frequency = start === end ? `每天 ${start}` : `每天 ${start} - ${end}`;
      } else if (freqType === "weekly") {
        const start = qs("#modal-task-weekly-time-start").value;
        const end = qs("#modal-task-weekly-time-end").value;
        const day = qs("#modal-task-weekday").value;
        frequency = start === end ? `每${day} ${start}` : `每${day} ${start} - ${end}`;
      } else {
        frequency = `${qs("#modal-task-range-start").value} 至 ${qs("#modal-task-range-end").value} 每天 ${qs("#modal-task-range-time-start").value} - ${qs("#modal-task-range-time-end").value}`;
      }
      state.tasks.unshift({
        name: qs("#modal-task-name").value,
        platform: qs("#modal-task-platform").value,
        frequency,
        status: "预留"
      });
      renderTasks();
      showToast("任务已添加");
    }
  });

  // Wire frequency type toggle
  const freqType = qs("#modal-freq-type");
  if (freqType) {
    freqType.addEventListener("change", () => {
      const v = freqType.value;
      qs("#freq-daily").style.display = v === "daily" ? "" : "none";
      qs("#freq-weekly").style.display = v === "weekly" ? "" : "none";
      qs("#freq-range").style.display = v === "range" ? "" : "none";
    });
  }
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

function regenerateImage(type) {
  const gallery = qs(`.image-gallery[data-gallery="${type}"]`);
  if (!gallery) return;
  const existingCount = gallery.querySelectorAll(".image-thumb").length;
  const thumb = document.createElement("div");
  thumb.className = "image-thumb";
  const src = type === "cover" ? "./assets/cover-ai-accounting.svg" : (existingCount % 2 === 0 ? "./assets/inline-cost.svg" : "./assets/inline-flow.svg");
  thumb.innerHTML = `<img src="${src}" alt="${type === "cover" ? "封面图" : "正文配图"}方案${existingCount + 1}" /><div class="thumb-check">&#10003;</div>`;
  gallery.appendChild(thumb);
  showToast(`已生成新${type === "cover" ? "封面图" : "配图"}方案，点击图片可勾选使用`);
}

function toggleImageThumb(thumb) {
  thumb.classList.toggle("selected");
}

function moveSectionUp(item) {
  const prev = item.previousElementSibling;
  if (prev) {
    item.parentElement.insertBefore(item, prev);
    updateLayoutPreview();
    showToast("段落已上移");
  }
}

function moveSectionDown(item) {
  const next = item.nextElementSibling;
  if (next) {
    item.parentElement.insertBefore(next, item);
    updateLayoutPreview();
    showToast("段落已下移");
  }
}

function updateLayoutPreview() {
  const preview = qs("#layout-preview");
  if (!preview) return;
  const items = qsa(".layout-section-item");
  let html = `<img src="./assets/cover-ai-accounting.svg" alt="封面图预览" />`;
  items.forEach((item) => {
    const section = item.dataset.section;
    const textEl = item.querySelector("input, textarea");
    const text = textEl ? textEl.value : "";
    const imgRow = item.querySelector(".section-img-row img");
    if (section === "title") {
      html += `<h4>${text}</h4>`;
    } else if (section === "intro") {
      html += `<p class="preview-intro">${text}</p>`;
    } else if (section === "cta") {
      html += `<p class="preview-cta">${text}</p>`;
    } else {
      if (imgRow) html += `<img src="${imgRow.src}" alt="正文配图" />`;
      html += `<p>${text}</p>`;
    }
  });
  preview.innerHTML = html;
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

    if (target.matches("[data-action='next-creation-step']")) {
      advanceCreationStage();
    }

    if (target.matches("[data-action='prev-creation-step']")) {
      goBackCreationStage();
    }

    if (target.matches("[data-action='regenerate-image']")) {
      regenerateImage(target.dataset.type);
      saveCurrentDraft(true);
    }

    const thumb = target.closest(".image-thumb");
    if (thumb) {
      toggleImageThumb(thumb);
    }

    if (target.matches("[data-action='move-section-up']")) {
      const item = target.closest(".layout-section-item");
      if (item) moveSectionUp(item);
    }

    if (target.matches("[data-action='move-section-down']")) {
      const item = target.closest(".layout-section-item");
      if (item) moveSectionDown(item);
    }

    if (target.matches("[data-action='restore-draft']")) {
      restoreDraft(Number(target.dataset.index));
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

  document.addEventListener("input", (event) => {
    if (event.target.closest(".layout-section-item")) {
      updateLayoutPreview();
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-role='draft-start'], [data-role='draft-end']")) {
      const start = qs("[data-role='draft-start']").value;
      const end = qs("[data-role='draft-end']").value;
      renderDrafts(start, end);
      showToast("已自动筛选草稿");
    }

    if (event.target.id === "publish-platform") {
      const platform = event.target.value;
      const accountSelect = qs("#publish-account");
      if (!accountSelect) return;
      const filtered = state.accounts.filter((a) => platform === "全平台" || a.platform === platform);
      accountSelect.innerHTML = filtered.length
        ? filtered.map((a) => `<option>${a.name}</option>`).join("")
        : `<option>暂无匹配账号</option>`;
    }
  });
}

function init() {
  renderHotspots();
  renderAiTopics();
  renderAiTopicChat();
  renderDrafts();
  renderTasks();
  renderReview();
  renderAccounts();
  setCreationStage("article");
  wireEvents();
}

init();
