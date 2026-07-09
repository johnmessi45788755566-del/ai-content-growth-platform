# AI Content Growth Platform MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working internal MVP that turns recommended hotspots or natural-language topic requests into a WeChat public-account article package with title, outline, body, cover-image prompt, inline-image prompts, CTA, private-message reply, draft status, schedule, and manual review metrics.

**Architecture:** Start with a Next.js TypeScript app using deterministic local services instead of live AI calls. Keep domain logic in pure TypeScript modules with unit tests, expose it through thin API routes, and build focused pages for topic selection, content package creation, draft management, and metrics entry.

**Tech Stack:** Next.js App Router, React, TypeScript, Tailwind CSS, Vitest, Testing Library, Playwright, Prisma, SQLite.

---

## File Structure

Create these files and keep responsibilities narrow:

- `package.json` - scripts and dependencies.
- `tsconfig.json` - strict TypeScript settings.
- `next.config.mjs` - Next.js config.
- `postcss.config.mjs` - PostCSS config for Tailwind.
- `tailwind.config.ts` - Tailwind content scanning config.
- `vitest.config.ts` - unit test config.
- `playwright.config.ts` - end-to-end test config.
- `prisma/schema.prisma` - SQLite data model for tenants, brand profiles, references, drafts, schedules, and metrics.
- `src/app/layout.tsx` - app shell metadata and global layout.
- `src/app/page.tsx` - redirect or link to the topic workspace.
- `src/app/topics/page.tsx` - hotspot list, natural-language topic request, and candidate selection.
- `src/app/create/[topicId]/page.tsx` - article package creation and editing view.
- `src/app/drafts/page.tsx` - draft list, schedule state, and manual status updates.
- `src/app/metrics/page.tsx` - manual performance entry and review summary.
- `src/app/settings/page.tsx` - brand profile, audience, CTA, and visual style settings.
- `src/app/api/topics/route.ts` - API for hotspot and AI topic candidates.
- `src/app/api/content-packages/route.ts` - API for deterministic content package generation.
- `src/app/api/drafts/route.ts` - API for saving and listing drafts.
- `src/app/api/metrics/route.ts` - API for manual metrics entry.
- `src/domain/topics/types.ts` - topic, hotspot, scoring, and natural-language request types.
- `src/domain/topics/fixtures.ts` - V0.1 seed hotspots for AI accounting and small-business owners.
- `src/domain/topics/scoring.ts` - topic scoring and risk scoring.
- `src/domain/topics/recommend.ts` - two-path topic candidate generation.
- `src/domain/references/types.ts` - reference article and structure analysis types.
- `src/domain/references/analyze.ts` - structure extraction from pasted reference content.
- `src/domain/content/types.ts` - strategy, article package, CTA, image prompt, and draft types.
- `src/domain/content/generate.ts` - deterministic article package generator.
- `src/domain/content/visuals.ts` - image prompt generation from title and body.
- `src/domain/content/risk.ts` - content and copyright risk checks.
- `src/domain/metrics/types.ts` - process and result metric types.
- `src/domain/metrics/review.ts` - performance summary and learning signals.
- `src/lib/db.ts` - Prisma client singleton.
- `src/lib/seed.ts` - default tenant, brand profile, hotspots, and internal references.
- `src/components/AppNav.tsx` - main navigation.
- `src/components/ScoreBadge.tsx` - reusable score display.
- `src/components/TopicCard.tsx` - reusable candidate card.
- `src/components/ArticlePackageEditor.tsx` - article package editor.
- `src/components/ImagePromptPanel.tsx` - cover and inline image prompt review.
- `src/components/RiskPanel.tsx` - content risk and copyright warnings.
- `tests/unit/topics.test.ts` - topic scoring and recommendation tests.
- `tests/unit/references.test.ts` - reference structure analysis tests.
- `tests/unit/content.test.ts` - content package and visual prompt tests.
- `tests/unit/metrics.test.ts` - manual metrics review tests.
- `tests/e2e/mvp-flow.spec.ts` - full hotspot-to-draft-to-metrics flow.

## Task 1: Scaffold The Next.js App

**Files:**
- Create: `package.json`
- Create: `tsconfig.json`
- Create: `next.config.mjs`
- Create: `postcss.config.mjs`
- Create: `tailwind.config.ts`
- Create: `vitest.config.ts`
- Create: `playwright.config.ts`
- Create: `src/app/layout.tsx`
- Create: `src/app/page.tsx`
- Create: `src/app/globals.css`

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "ai-content-growth-platform",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:e2e": "playwright test",
    "db:push": "prisma db push",
    "db:seed": "tsx src/lib/seed.ts"
  },
  "dependencies": {
    "@prisma/client": "^5.22.0",
    "next": "^15.0.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "zod": "^3.24.0"
  },
  "devDependencies": {
    "@playwright/test": "^1.49.0",
    "@testing-library/jest-dom": "^6.6.0",
    "@testing-library/react": "^16.1.0",
    "@types/node": "^22.10.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "autoprefixer": "^10.4.20",
    "eslint": "^9.17.0",
    "eslint-config-next": "^15.0.0",
    "jsdom": "^25.0.1",
    "prisma": "^5.22.0",
    "tailwindcss": "^3.4.17",
    "tsx": "^4.19.2",
    "typescript": "^5.7.0",
    "vitest": "^2.1.8"
  }
}
```

- [ ] **Step 2: Create TypeScript and tool configs**

`tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "es2022"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

`next.config.mjs`:

```js
const nextConfig = {};

export default nextConfig;
```

`postcss.config.mjs`:

```js
const config = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {}
  }
};

export default config;
```

`tailwind.config.ts`:

```ts
import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {}
  },
  plugins: []
};

export default config;
```

`vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    include: ["tests/unit/**/*.test.ts", "tests/unit/**/*.test.tsx"]
  },
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname
    }
  }
});
```

`playwright.config.ts`:

```ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "tests/e2e",
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: true
  },
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "on-first-retry"
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ]
});
```

- [ ] **Step 3: Create app shell files**

`src/app/globals.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  color: #17203a;
  background: #f6f7fb;
}

body {
  margin: 0;
  font-family: Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
}
```

`src/app/layout.tsx`:

```tsx
import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "AI 内容增长工作平台",
  description: "热点选题、爆款参考、公众号图文素材包和复盘工作台"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
```

`src/app/page.tsx`:

```tsx
import Link from "next/link";

export default function HomePage() {
  return (
    <main style={{ padding: 32 }}>
      <h1>AI 内容增长工作平台</h1>
      <p>从热点和话题出发，生成公众号图文素材包。</p>
      <Link href="/topics">进入热点选题</Link>
    </main>
  );
}
```

- [ ] **Step 4: Install dependencies**

Run: `npm install`

Expected: dependencies install and `package-lock.json` is created.

- [ ] **Step 5: Verify the scaffold**

Run: `npm run test`

Expected: Vitest starts and reports no matching tests or 0 tests without TypeScript errors.

Run: `npm run build`

Expected: Next.js build completes.

- [ ] **Step 6: Commit**

```bash
git add package.json package-lock.json tsconfig.json next.config.mjs postcss.config.mjs tailwind.config.ts vitest.config.ts playwright.config.ts src/app
git commit -m "chore: scaffold content growth app"
```

## Task 2: Add Domain Models And Topic Recommendation Logic

**Files:**
- Create: `src/domain/topics/types.ts`
- Create: `src/domain/topics/fixtures.ts`
- Create: `src/domain/topics/scoring.ts`
- Create: `src/domain/topics/recommend.ts`
- Create: `tests/unit/topics.test.ts`

- [ ] **Step 1: Write failing tests**

`tests/unit/topics.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { recommendFromHotspots, recommendFromNaturalLanguage } from "@/domain/topics/recommend";
import { scoreTopic } from "@/domain/topics/scoring";
import { seedHotspots } from "@/domain/topics/fixtures";

describe("topic recommendation", () => {
  it("scores topics by heat, audience match, conversion fit, and risk", () => {
    const score = scoreTopic({
      heat: 88,
      audienceFit: 94,
      conversionFit: 91,
      risk: 20
    });

    expect(score.total).toBe(86);
    expect(score.label).toBe("优先创作");
  });

  it("recommends hotspot-based topics for small-business owners", () => {
    const topics = recommendFromHotspots(seedHotspots, "small-business-owner");

    expect(topics[0].source).toBe("hotspot");
    expect(topics[0].title).toContain("AI");
    expect(topics[0].audience).toBe("小微企业老板");
    expect(topics[0].recommendedAction).toBe("查看热点详情并进入结构仿写");
  });

  it("turns natural-language requirements into selectable topics", () => {
    const topics = recommendFromNaturalLanguage({
      intent: "写一篇让老板觉得财务记账应该被 AI 改造的文章",
      customer: "小微企业老板",
      product: "AI 记账工具",
      goal: "私信进群"
    });

    expect(topics).toHaveLength(3);
    expect(topics[0].source).toBe("natural-language");
    expect(topics[0].conversionAngle).toContain("私信");
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run: `npm run test -- tests/unit/topics.test.ts`

Expected: FAIL because `@/domain/topics/recommend` and related modules do not exist.

- [ ] **Step 3: Implement topic domain files**

`src/domain/topics/types.ts`:

```ts
export type TopicSource = "hotspot" | "natural-language";

export type AudienceSegment = "small-business-owner" | "enterprise-media-team";

export type TopicScoreInput = {
  heat: number;
  audienceFit: number;
  conversionFit: number;
  risk: number;
};

export type TopicScore = {
  total: number;
  label: "优先创作" | "谨慎创作" | "不建议创作";
};

export type Hotspot = {
  id: string;
  industry: string;
  title: string;
  heat: number;
  trend: "rising" | "stable" | "falling";
  keywords: string[];
};

export type TopicCandidate = {
  id: string;
  source: TopicSource;
  title: string;
  audience: string;
  articleType: "热点解读型" | "干货攻略型" | "观点评论型" | "品牌/产品铺垫型";
  score: TopicScore;
  painPoint: string;
  conversionAngle: string;
  riskNote: string;
  recommendedAction: string;
};

export type NaturalLanguageTopicRequest = {
  intent: string;
  customer: string;
  product: string;
  goal: string;
};
```

`src/domain/topics/fixtures.ts`:

```ts
import type { Hotspot } from "./types";

export const seedHotspots: Hotspot[] = [
  {
    id: "ai-finance-efficiency",
    industry: "企业服务",
    title: "AI 正在重塑小微企业财务效率",
    heat: 92,
    trend: "rising",
    keywords: ["AI", "财务", "降本增效", "小微企业"]
  },
  {
    id: "invoice-automation",
    industry: "财税",
    title: "票据和流水自动化成为老板降本新抓手",
    heat: 86,
    trend: "rising",
    keywords: ["票据", "流水", "自动化", "记账"]
  },
  {
    id: "manual-accounting-cost",
    industry: "经营管理",
    title: "人工记账成本上升，小微企业开始寻找替代方案",
    heat: 78,
    trend: "stable",
    keywords: ["人工成本", "记账", "老板", "效率"]
  }
];
```

`src/domain/topics/scoring.ts`:

```ts
import type { TopicScore, TopicScoreInput } from "./types";

export function scoreTopic(input: TopicScoreInput): TopicScore {
  const total = Math.round(
    input.heat * 0.3 +
      input.audienceFit * 0.3 +
      input.conversionFit * 0.3 -
      input.risk * 0.1
  );

  if (total >= 80) {
    return { total, label: "优先创作" };
  }

  if (total >= 60) {
    return { total, label: "谨慎创作" };
  }

  return { total, label: "不建议创作" };
}
```

`src/domain/topics/recommend.ts`:

```ts
import type {
  AudienceSegment,
  Hotspot,
  NaturalLanguageTopicRequest,
  TopicCandidate
} from "./types";
import { scoreTopic } from "./scoring";

export function recommendFromHotspots(
  hotspots: Hotspot[],
  audience: AudienceSegment
): TopicCandidate[] {
  const audienceName = audience === "small-business-owner" ? "小微企业老板" : "企业新媒体团队";

  return hotspots
    .map((hotspot) => ({
      id: `topic-${hotspot.id}`,
      source: "hotspot" as const,
      title: hotspot.title,
      audience: audienceName,
      articleType: "品牌/产品铺垫型" as const,
      score: scoreTopic({
        heat: hotspot.heat,
        audienceFit: hotspot.keywords.includes("小微企业") || hotspot.keywords.includes("老板") ? 94 : 78,
        conversionFit: hotspot.keywords.includes("记账") || hotspot.keywords.includes("财务") ? 91 : 76,
        risk: 22
      }),
      painPoint: "财务人力成本高、票据流水处理慢、老板看不到实时经营数据。",
      conversionAngle: "引导读者私信关键词，进入老板 AI 咨询群。",
      riskNote: "避免夸大 AI 替代和企业淘汰风险，用成本与效率事实表达紧迫感。",
      recommendedAction: "查看热点详情并进入结构仿写"
    }))
    .sort((a, b) => b.score.total - a.score.total);
}

export function recommendFromNaturalLanguage(
  request: NaturalLanguageTopicRequest
): TopicCandidate[] {
  const base = {
    source: "natural-language" as const,
    audience: request.customer,
    score: scoreTopic({ heat: 82, audienceFit: 90, conversionFit: 88, risk: 24 }),
    riskNote: "使用中度紧迫表达，不编造数据，不做绝对化承诺。",
    recommendedAction: "选择选题并生成公众号图文素材包"
  };

  return [
    {
      ...base,
      id: "nl-topic-cost",
      title: "为什么小微企业老板应该先用 AI 改造记账",
      articleType: "观点评论型",
      painPoint: "财务和记账人力成本持续存在，重复处理挤占管理精力。",
      conversionAngle: `围绕${request.goal}设计结尾，邀请读者私信领取 AI 财务效率建议。`
    },
    {
      ...base,
      id: "nl-topic-invoice",
      title: "发票、流水、账本三件事，正在被 AI 重新做一遍",
      articleType: "热点解读型",
      painPoint: "票据和流水分散，人工处理慢，老板月底才知道经营结果。",
      conversionAngle: `把${request.product}作为后续群内交流方向，先完成私信进群。`
    },
    {
      ...base,
      id: "nl-topic-checklist",
      title: "小微企业财务自动化，从这 5 个环节开始",
      articleType: "干货攻略型",
      painPoint: "老板知道要降本，但不知道先从哪个财务环节动手。",
      conversionAngle: "结尾引导私信关键词，进群获取财务自动化清单。"
    }
  ];
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `npm run test -- tests/unit/topics.test.ts`

Expected: PASS with 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/domain/topics tests/unit/topics.test.ts
git commit -m "feat: add topic recommendation domain"
```

## Task 3: Add Reference Structure Analysis

**Files:**
- Create: `src/domain/references/types.ts`
- Create: `src/domain/references/analyze.ts`
- Create: `tests/unit/references.test.ts`

- [ ] **Step 1: Write failing tests**

`tests/unit/references.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { analyzeReferenceArticle } from "@/domain/references/analyze";

describe("reference article analysis", () => {
  it("extracts structure without copying source content", () => {
    const analysis = analyzeReferenceArticle({
      title: "老板再不看懂 AI，财务成本会越来越高",
      body: "开头制造经营压力。第二段讲人工记账。第三段讲票据流水。结尾引导私信进群。"
    });

    expect(analysis.titleHook).toBe("焦虑型标题：把 AI 认知和财务成本绑定");
    expect(analysis.openingPattern).toContain("经营压力");
    expect(analysis.conversionPattern).toBe("私信关键词进群");
    expect(analysis.copyWarning).toContain("只参考结构");
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run: `npm run test -- tests/unit/references.test.ts`

Expected: FAIL because `analyzeReferenceArticle` is not defined.

- [ ] **Step 3: Implement reference analysis**

`src/domain/references/types.ts`:

```ts
export type ReferenceArticleInput = {
  title: string;
  body: string;
};

export type ReferenceStructureAnalysis = {
  titleHook: string;
  openingPattern: string;
  argumentPattern: string;
  conversionPattern: string;
  copyWarning: string;
};
```

`src/domain/references/analyze.ts`:

```ts
import type { ReferenceArticleInput, ReferenceStructureAnalysis } from "./types";

export function analyzeReferenceArticle(input: ReferenceArticleInput): ReferenceStructureAnalysis {
  const text = `${input.title}\n${input.body}`;
  const mentionsPrivateMessage = text.includes("私信") || text.includes("进群");
  const mentionsCost = text.includes("成本") || text.includes("降本");

  return {
    titleHook: mentionsCost
      ? "焦虑型标题：把 AI 认知和财务成本绑定"
      : "趋势型标题：把热点变化和老板决策绑定",
    openingPattern: text.includes("压力")
      ? "开头先呈现经营压力，再进入具体财务场景"
      : "开头先呈现行业变化，再指出老板需要关注的动作",
    argumentPattern: "按痛点、场景、成本账、解决路径组织论证",
    conversionPattern: mentionsPrivateMessage ? "私信关键词进群" : "引导读者了解后续方案",
    copyWarning: "只参考结构、节奏和转化方式，不复制原文表达"
  };
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `npm run test -- tests/unit/references.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/domain/references tests/unit/references.test.ts
git commit -m "feat: add reference structure analysis"
```

## Task 4: Add Content Package Generation

**Files:**
- Create: `src/domain/content/types.ts`
- Create: `src/domain/content/risk.ts`
- Create: `src/domain/content/visuals.ts`
- Create: `src/domain/content/generate.ts`
- Create: `tests/unit/content.test.ts`

- [ ] **Step 1: Write failing tests**

`tests/unit/content.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { generateArticlePackage } from "@/domain/content/generate";

describe("content package generation", () => {
  it("creates a WeChat article package with text, visuals, CTA, and risk checks", () => {
    const result = generateArticlePackage({
      topicTitle: "为什么小微企业老板应该先用 AI 改造记账",
      audience: "小微企业老板",
      painPoint: "财务人力成本高、票据流水处理慢",
      conversionAngle: "私信关键词进群",
      brandTone: "稳健、直接、面向老板",
      visualStyle: "商务科技感"
    });

    expect(result.primaryTitle).toContain("AI");
    expect(result.body).toContain("小微企业老板");
    expect(result.cta).toContain("私信");
    expect(result.privateMessageReply).toContain("进群");
    expect(result.coverImagePrompt).toContain("商务科技感");
    expect(result.inlineImagePrompts).toHaveLength(2);
    expect(result.riskWarnings).toContain("禁止编造财务数据和产品效果");
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run: `npm run test -- tests/unit/content.test.ts`

Expected: FAIL because content modules do not exist.

- [ ] **Step 3: Implement content domain**

`src/domain/content/types.ts`:

```ts
export type ArticlePackageInput = {
  topicTitle: string;
  audience: string;
  painPoint: string;
  conversionAngle: string;
  brandTone: string;
  visualStyle: string;
};

export type ArticlePackage = {
  primaryTitle: string;
  alternativeTitles: string[];
  summary: string;
  outline: string[];
  body: string;
  cta: string;
  privateMessageReply: string;
  coverImagePrompt: string;
  inlineImagePrompts: string[];
  riskWarnings: string[];
};
```

`src/domain/content/risk.ts`:

```ts
export function getDefaultRiskWarnings(): string[] {
  return [
    "禁止编造财务数据和产品效果",
    "避免使用不会用 AI 就会被淘汰等绝对化恐吓表达",
    "参考爆款时只学习结构，不复制原文内容",
    "图片不得生成虚假产品截图或冒用第三方品牌"
  ];
}
```

`src/domain/content/visuals.ts`:

```ts
export function buildCoverImagePrompt(title: string, audience: string, visualStyle: string): string {
  return `公众号封面图，主题：${title}，读者：${audience}，风格：${visualStyle}，画面包含企业老板、财务报表、AI 自动化元素，避免虚假产品截图。`;
}

export function buildInlineImagePrompts(title: string, visualStyle: string): string[] {
  return [
    `正文配图 1，展示${title}中的痛点场景，风格：${visualStyle}，突出人工记账、票据和流水分散。`,
    `正文配图 2，展示 AI 自动化记账流程，风格：${visualStyle}，突出降本增效和流程清晰。`
  ];
}
```

`src/domain/content/generate.ts`:

```ts
import type { ArticlePackage, ArticlePackageInput } from "./types";
import { getDefaultRiskWarnings } from "./risk";
import { buildCoverImagePrompt, buildInlineImagePrompts } from "./visuals";

export function generateArticlePackage(input: ArticlePackageInput): ArticlePackage {
  return {
    primaryTitle: input.topicTitle,
    alternativeTitles: [
      "小微企业的下一轮降本，可能先从记账开始",
      "老板别只盯销售，财务效率也该被 AI 改造了",
      "票据、流水、账本，AI 正在接手最重复的财务工作"
    ],
    summary: `面向${input.audience}，用中度紧迫感解释${input.painPoint}，自然引出 AI 记账和私域进群。`,
    outline: [
      "开头：用 AI 和经营成本变化制造认知紧迫感",
      `痛点：展开${input.painPoint}`,
      "方案：解释 AI 适合先改造票据、流水和记账流程",
      "转化：引导私信关键词进入老板 AI 咨询群"
    ],
    body: `很多${input.audience}已经感受到，AI 不是遥远概念，而是在改变企业里最重复、最耗人力的工作。\n\n${input.painPoint}，这些问题单独看不大，但每个月都会消耗老板的时间和现金流。\n\n财务记账适合先被 AI 改造，因为票据、流水、分类、对账都有清晰流程。老板不需要先做复杂系统升级，可以先从高频重复环节开始。\n\n这篇文章不建议把 AI 说成万能工具，而是把它放回具体经营场景：少一点重复录入，快一点看到数据，少一点人工沟通成本。`,
    cta: `如果你也想了解 AI 记账怎么帮小微企业省人省钱，可以私信关键词【AI记账】，我们邀请你进入老板 AI 咨询群。`,
    privateMessageReply: "收到，欢迎关注 AI 记账和企业降本。请留下你的行业和企业规模，我们会邀请你进群交流。",
    coverImagePrompt: buildCoverImagePrompt(input.topicTitle, input.audience, input.visualStyle),
    inlineImagePrompts: buildInlineImagePrompts(input.topicTitle, input.visualStyle),
    riskWarnings: getDefaultRiskWarnings()
  };
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `npm run test -- tests/unit/content.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/domain/content tests/unit/content.test.ts
git commit -m "feat: generate wechat content packages"
```

## Task 5: Add Persistence Model And Seed Data

**Files:**
- Create: `prisma/schema.prisma`
- Create: `src/lib/db.ts`
- Create: `src/lib/seed.ts`

- [ ] **Step 1: Create Prisma schema**

`prisma/schema.prisma`:

```prisma
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "sqlite"
  url      = env("DATABASE_URL")
}

model Tenant {
  id           String        @id
  name         String
  brandProfile BrandProfile?
  drafts       Draft[]
  metrics      Metric[]
  createdAt    DateTime      @default(now())
}

model BrandProfile {
  id           String @id
  tenantId     String @unique
  tenant       Tenant @relation(fields: [tenantId], references: [id])
  audience     String
  brandTone    String
  visualStyle  String
  ctaKeyword   String
  privateReply String
}

model Draft {
  id                  String   @id
  tenantId            String
  tenant              Tenant   @relation(fields: [tenantId], references: [id])
  topicTitle          String
  primaryTitle        String
  body                String
  cta                 String
  privateMessageReply String
  coverImagePrompt    String
  inlineImagePrompts  String
  status              String
  scheduledFor        DateTime?
  createdAt           DateTime @default(now())
  updatedAt           DateTime @updatedAt
}

model Metric {
  id              String   @id
  tenantId        String
  tenant          Tenant   @relation(fields: [tenantId], references: [id])
  draftId         String
  reads           Int
  shares          Int
  likes           Int
  wows            Int
  privateMessages Int
  groupJoins      Int
  leadQuality     String
  createdAt       DateTime @default(now())
}
```

- [ ] **Step 2: Add database client and seed**

`src/lib/db.ts`:

```ts
import { PrismaClient } from "@prisma/client";

const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const prisma = globalForPrisma.prisma ?? new PrismaClient();

if (process.env.NODE_ENV !== "production") {
  globalForPrisma.prisma = prisma;
}
```

`src/lib/seed.ts`:

```ts
import { prisma } from "./db";

async function main() {
  await prisma.tenant.upsert({
    where: { id: "internal" },
    update: {},
    create: {
      id: "internal",
      name: "内部公众号增长团队",
      brandProfile: {
        create: {
          id: "internal-brand",
          audience: "小微企业老板",
          brandTone: "稳健、直接、面向老板",
          visualStyle: "商务科技感",
          ctaKeyword: "AI记账",
          privateReply: "收到，欢迎关注 AI 记账和企业降本。请留下你的行业和企业规模，我们会邀请你进群交流。"
        }
      }
    }
  });
}

main()
  .then(async () => {
    await prisma.$disconnect();
  })
  .catch(async (error) => {
    console.error(error);
    await prisma.$disconnect();
    process.exit(1);
  });
```

- [ ] **Step 3: Initialize local database**

Create `.env`:

```bash
DATABASE_URL="file:./dev.db"
```

Run: `npm run db:push`

Expected: Prisma creates SQLite tables.

Run: `npm run db:seed`

Expected: seed completes without errors.

- [ ] **Step 4: Commit**

```bash
git add prisma/schema.prisma src/lib/db.ts src/lib/seed.ts .env
git commit -m "feat: add persistence model and seed data"
```

## Task 6: Add API Routes For Topics, Content Packages, Drafts, And Metrics

**Files:**
- Create: `src/app/api/topics/route.ts`
- Create: `src/app/api/content-packages/route.ts`
- Create: `src/app/api/drafts/route.ts`
- Create: `src/app/api/metrics/route.ts`

- [ ] **Step 1: Create topic API**

`src/app/api/topics/route.ts`:

```ts
import { NextResponse } from "next/server";
import { seedHotspots } from "@/domain/topics/fixtures";
import { recommendFromHotspots, recommendFromNaturalLanguage } from "@/domain/topics/recommend";

export async function GET() {
  return NextResponse.json({
    topics: recommendFromHotspots(seedHotspots, "small-business-owner")
  });
}

export async function POST(request: Request) {
  const body = await request.json();
  return NextResponse.json({
    topics: recommendFromNaturalLanguage({
      intent: String(body.intent ?? ""),
      customer: String(body.customer ?? "小微企业老板"),
      product: String(body.product ?? "AI 记账工具"),
      goal: String(body.goal ?? "私信进群")
    })
  });
}
```

- [ ] **Step 2: Create content package API**

`src/app/api/content-packages/route.ts`:

```ts
import { NextResponse } from "next/server";
import { generateArticlePackage } from "@/domain/content/generate";

export async function POST(request: Request) {
  const body = await request.json();
  return NextResponse.json({
    package: generateArticlePackage({
      topicTitle: String(body.topicTitle),
      audience: String(body.audience ?? "小微企业老板"),
      painPoint: String(body.painPoint ?? "财务人力成本高、票据流水处理慢"),
      conversionAngle: String(body.conversionAngle ?? "私信关键词进群"),
      brandTone: String(body.brandTone ?? "稳健、直接、面向老板"),
      visualStyle: String(body.visualStyle ?? "商务科技感")
    })
  });
}
```

- [ ] **Step 3: Create draft API**

`src/app/api/drafts/route.ts`:

```ts
import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET() {
  const drafts = await prisma.draft.findMany({
    where: { tenantId: "internal" },
    orderBy: { createdAt: "desc" }
  });

  return NextResponse.json({ drafts });
}

export async function POST(request: Request) {
  const body = await request.json();
  const draft = await prisma.draft.create({
    data: {
      id: crypto.randomUUID(),
      tenantId: "internal",
      topicTitle: String(body.topicTitle),
      primaryTitle: String(body.primaryTitle),
      body: String(body.body),
      cta: String(body.cta),
      privateMessageReply: String(body.privateMessageReply),
      coverImagePrompt: String(body.coverImagePrompt),
      inlineImagePrompts: JSON.stringify(body.inlineImagePrompts ?? []),
      status: String(body.status ?? "草稿")
    }
  });

  return NextResponse.json({ draft });
}
```

- [ ] **Step 4: Create metrics API**

`src/app/api/metrics/route.ts`:

```ts
import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function POST(request: Request) {
  const body = await request.json();
  const metric = await prisma.metric.create({
    data: {
      id: crypto.randomUUID(),
      tenantId: "internal",
      draftId: String(body.draftId),
      reads: Number(body.reads ?? 0),
      shares: Number(body.shares ?? 0),
      likes: Number(body.likes ?? 0),
      wows: Number(body.wows ?? 0),
      privateMessages: Number(body.privateMessages ?? 0),
      groupJoins: Number(body.groupJoins ?? 0),
      leadQuality: String(body.leadQuality ?? "未评估")
    }
  });

  return NextResponse.json({ metric });
}
```

- [ ] **Step 5: Verify API build**

Run: `npm run build`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/app/api
git commit -m "feat: add mvp api routes"
```

## Task 7: Build Shared UI Components

**Files:**
- Create: `src/components/AppNav.tsx`
- Create: `src/components/ScoreBadge.tsx`
- Create: `src/components/TopicCard.tsx`
- Create: `src/components/ImagePromptPanel.tsx`
- Create: `src/components/RiskPanel.tsx`

- [ ] **Step 1: Create navigation and reusable cards**

`src/components/AppNav.tsx`:

```tsx
import Link from "next/link";

const items = [
  ["热点选题", "/topics"],
  ["草稿排期", "/drafts"],
  ["数据复盘", "/metrics"],
  ["企业设置", "/settings"]
];

export function AppNav() {
  return (
    <nav className="flex gap-4 border-b bg-white px-6 py-4">
      {items.map(([label, href]) => (
        <Link key={href} href={href} className="font-medium text-slate-700">
          {label}
        </Link>
      ))}
    </nav>
  );
}
```

`src/components/ScoreBadge.tsx`:

```tsx
export function ScoreBadge({ score, label }: { score: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-2 rounded bg-indigo-50 px-2 py-1 text-sm text-indigo-700">
      <strong>{score}</strong>
      {label}
    </span>
  );
}
```

`src/components/TopicCard.tsx`:

```tsx
import { ScoreBadge } from "./ScoreBadge";
import type { TopicCandidate } from "@/domain/topics/types";

export function TopicCard({ topic, href }: { topic: TopicCandidate; href: string }) {
  return (
    <article className="rounded border bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-lg font-semibold">{topic.title}</h3>
        <ScoreBadge score={topic.score.total} label={topic.score.label} />
      </div>
      <p className="text-sm text-slate-600">{topic.painPoint}</p>
      <p className="mt-2 text-sm text-slate-600">{topic.conversionAngle}</p>
      <a className="mt-4 inline-block rounded bg-indigo-600 px-3 py-2 text-sm text-white" href={href}>
        {topic.recommendedAction}
      </a>
    </article>
  );
}
```

- [ ] **Step 2: Create visual and risk panels**

`src/components/ImagePromptPanel.tsx`:

```tsx
export function ImagePromptPanel({
  cover,
  inlinePrompts
}: {
  cover: string;
  inlinePrompts: string[];
}) {
  return (
    <section className="rounded border bg-white p-4">
      <h2 className="text-lg font-semibold">封面图与正文配图</h2>
      <p className="mt-3 rounded bg-slate-50 p-3 text-sm">{cover}</p>
      <div className="mt-3 grid gap-3">
        {inlinePrompts.map((prompt, index) => (
          <p key={prompt} className="rounded bg-slate-50 p-3 text-sm">
            正文配图 {index + 1}：{prompt}
          </p>
        ))}
      </div>
    </section>
  );
}
```

`src/components/RiskPanel.tsx`:

```tsx
export function RiskPanel({ warnings }: { warnings: string[] }) {
  return (
    <section className="rounded border border-amber-200 bg-amber-50 p-4">
      <h2 className="text-lg font-semibold text-amber-900">风险提示</h2>
      <ul className="mt-3 list-disc pl-5 text-sm text-amber-900">
        {warnings.map((warning) => (
          <li key={warning}>{warning}</li>
        ))}
      </ul>
    </section>
  );
}
```

- [ ] **Step 3: Verify build**

Run: `npm run build`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/components
git commit -m "feat: add shared workspace components"
```

## Task 8: Build Topic Selection Page

**Files:**
- Create: `src/app/topics/page.tsx`

- [ ] **Step 1: Implement page**

`src/app/topics/page.tsx`:

```tsx
import { AppNav } from "@/components/AppNav";
import { TopicCard } from "@/components/TopicCard";
import { seedHotspots } from "@/domain/topics/fixtures";
import { recommendFromHotspots, recommendFromNaturalLanguage } from "@/domain/topics/recommend";

export default function TopicsPage() {
  const hotspotTopics = recommendFromHotspots(seedHotspots, "small-business-owner");
  const aiTopics = recommendFromNaturalLanguage({
    intent: "写一篇让老板觉得财务记账应该被 AI 改造的文章",
    customer: "小微企业老板",
    product: "AI 记账工具",
    goal: "私信进群"
  });

  return (
    <>
      <AppNav />
      <main className="space-y-8 p-6">
        <header>
          <h1 className="text-2xl font-bold">热点选题</h1>
          <p className="mt-2 text-slate-600">从推荐热点直接仿写，或输入目标让 AI 推荐选题。</p>
        </header>

        <section className="rounded border bg-white p-4">
          <h2 className="text-lg font-semibold">自然语言选题</h2>
          <textarea
            className="mt-3 min-h-28 w-full rounded border p-3"
            defaultValue="我想写一篇给小微企业老板看的文章，主题是 AI 记账如何降低财务人力成本，目标是引导私信进群。"
          />
          <button className="mt-3 rounded bg-indigo-600 px-4 py-2 text-white">AI 推荐选题</button>
        </section>

        <section>
          <h2 className="mb-3 text-lg font-semibold">推荐热点选题</h2>
          <div className="grid gap-4">
            {hotspotTopics.map((topic) => (
              <TopicCard key={topic.id} topic={topic} href={`/create/${topic.id}`} />
            ))}
          </div>
        </section>

        <section>
          <h2 className="mb-3 text-lg font-semibold">AI 推荐选题</h2>
          <div className="grid gap-4">
            {aiTopics.map((topic) => (
              <TopicCard key={topic.id} topic={topic} href={`/create/${topic.id}`} />
            ))}
          </div>
        </section>
      </main>
    </>
  );
}
```

- [ ] **Step 2: Verify page build**

Run: `npm run build`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/app/topics/page.tsx
git commit -m "feat: add topic selection workspace"
```

## Task 9: Build Article Package Creation Page

**Files:**
- Create: `src/components/ArticlePackageEditor.tsx`
- Create: `src/app/create/[topicId]/page.tsx`

- [ ] **Step 1: Create editor component**

`src/components/ArticlePackageEditor.tsx`:

```tsx
import type { ArticlePackage } from "@/domain/content/types";

export function ArticlePackageEditor({ article }: { article: ArticlePackage }) {
  return (
    <section className="rounded border bg-white p-4">
      <h2 className="text-lg font-semibold">公众号图文素材包</h2>
      <label className="mt-4 block text-sm font-medium">主标题</label>
      <input className="mt-1 w-full rounded border p-2" defaultValue={article.primaryTitle} />

      <label className="mt-4 block text-sm font-medium">摘要</label>
      <textarea className="mt-1 min-h-20 w-full rounded border p-2" defaultValue={article.summary} />

      <label className="mt-4 block text-sm font-medium">正文</label>
      <textarea className="mt-1 min-h-80 w-full rounded border p-2" defaultValue={article.body} />

      <label className="mt-4 block text-sm font-medium">结尾 CTA</label>
      <textarea className="mt-1 min-h-20 w-full rounded border p-2" defaultValue={article.cta} />

      <label className="mt-4 block text-sm font-medium">私信回复话术</label>
      <textarea className="mt-1 min-h-20 w-full rounded border p-2" defaultValue={article.privateMessageReply} />
    </section>
  );
}
```

- [ ] **Step 2: Create generation page**

`src/app/create/[topicId]/page.tsx`:

```tsx
import { AppNav } from "@/components/AppNav";
import { ArticlePackageEditor } from "@/components/ArticlePackageEditor";
import { ImagePromptPanel } from "@/components/ImagePromptPanel";
import { RiskPanel } from "@/components/RiskPanel";
import { generateArticlePackage } from "@/domain/content/generate";

export default function CreatePage({ params }: { params: { topicId: string } }) {
  const article = generateArticlePackage({
    topicTitle: params.topicId.includes("invoice")
      ? "发票、流水、账本三件事，正在被 AI 重新做一遍"
      : "为什么小微企业老板应该先用 AI 改造记账",
    audience: "小微企业老板",
    painPoint: "财务人力成本高、票据流水处理慢",
    conversionAngle: "私信关键词进群",
    brandTone: "稳健、直接、面向老板",
    visualStyle: "商务科技感"
  });

  return (
    <>
      <AppNav />
      <main className="grid gap-6 p-6 lg:grid-cols-[1.4fr_.9fr]">
        <ArticlePackageEditor article={article} />
        <aside className="space-y-4">
          <ImagePromptPanel cover={article.coverImagePrompt} inlinePrompts={article.inlineImagePrompts} />
          <RiskPanel warnings={article.riskWarnings} />
          <button className="w-full rounded bg-indigo-600 px-4 py-3 text-white">保存为草稿</button>
        </aside>
      </main>
    </>
  );
}
```

- [ ] **Step 3: Verify page build**

Run: `npm run build`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/components/ArticlePackageEditor.tsx src/app/create
git commit -m "feat: add article package creation page"
```

## Task 10: Build Drafts And Metrics Pages

**Files:**
- Create: `src/app/drafts/page.tsx`
- Create: `src/app/metrics/page.tsx`
- Create: `src/domain/metrics/types.ts`
- Create: `src/domain/metrics/review.ts`
- Create: `tests/unit/metrics.test.ts`

- [ ] **Step 1: Write metrics test**

`tests/unit/metrics.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { summarizeMetric } from "@/domain/metrics/review";

describe("metrics review", () => {
  it("summarizes private-domain conversion signals", () => {
    const summary = summarizeMetric({
      reads: 1200,
      shares: 90,
      likes: 60,
      wows: 20,
      privateMessages: 18,
      groupJoins: 12,
      leadQuality: "中"
    });

    expect(summary.shareRate).toBe("7.50%");
    expect(summary.groupJoinRate).toBe("66.67%");
    expect(summary.learning).toContain("私信");
  });
});
```

- [ ] **Step 2: Implement metrics domain**

`src/domain/metrics/types.ts`:

```ts
export type ManualMetricInput = {
  reads: number;
  shares: number;
  likes: number;
  wows: number;
  privateMessages: number;
  groupJoins: number;
  leadQuality: string;
};

export type MetricSummary = {
  shareRate: string;
  groupJoinRate: string;
  learning: string;
};
```

`src/domain/metrics/review.ts`:

```ts
import type { ManualMetricInput, MetricSummary } from "./types";

function percent(numerator: number, denominator: number): string {
  if (denominator === 0) return "0.00%";
  return `${((numerator / denominator) * 100).toFixed(2)}%`;
}

export function summarizeMetric(input: ManualMetricInput): MetricSummary {
  return {
    shareRate: percent(input.shares, input.reads),
    groupJoinRate: percent(input.groupJoins, input.privateMessages),
    learning: `本篇私信 ${input.privateMessages} 次，进群 ${input.groupJoins} 人，线索质量为${input.leadQuality}。`
  };
}
```

- [ ] **Step 3: Create drafts page**

`src/app/drafts/page.tsx`:

```tsx
import { AppNav } from "@/components/AppNav";

export default function DraftsPage() {
  return (
    <>
      <AppNav />
      <main className="p-6">
        <h1 className="text-2xl font-bold">草稿排期</h1>
        <div className="mt-6 rounded border bg-white p-4">
          <h2 className="text-lg font-semibold">为什么小微企业老板应该先用 AI 改造记账</h2>
          <p className="mt-2 text-sm text-slate-600">状态：草稿 | 负责人：内容团队 | 发布排期：待确认</p>
          <button className="mt-4 rounded bg-indigo-600 px-4 py-2 text-white">确认排期</button>
        </div>
      </main>
    </>
  );
}
```

- [ ] **Step 4: Create metrics page**

`src/app/metrics/page.tsx`:

```tsx
import { AppNav } from "@/components/AppNav";
import { summarizeMetric } from "@/domain/metrics/review";

export default function MetricsPage() {
  const summary = summarizeMetric({
    reads: 1200,
    shares: 90,
    likes: 60,
    wows: 20,
    privateMessages: 18,
    groupJoins: 12,
    leadQuality: "中"
  });

  return (
    <>
      <AppNav />
      <main className="p-6">
        <h1 className="text-2xl font-bold">数据复盘</h1>
        <section className="mt-6 grid gap-4 md:grid-cols-3">
          <div className="rounded border bg-white p-4">
            <p className="text-sm text-slate-500">分享率</p>
            <strong className="text-2xl">{summary.shareRate}</strong>
          </div>
          <div className="rounded border bg-white p-4">
            <p className="text-sm text-slate-500">私信到进群转化</p>
            <strong className="text-2xl">{summary.groupJoinRate}</strong>
          </div>
          <div className="rounded border bg-white p-4">
            <p className="text-sm text-slate-500">复盘结论</p>
            <p>{summary.learning}</p>
          </div>
        </section>
      </main>
    </>
  );
}
```

- [ ] **Step 5: Run tests and build**

Run: `npm run test -- tests/unit/metrics.test.ts`

Expected: PASS.

Run: `npm run build`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/domain/metrics src/app/drafts src/app/metrics tests/unit/metrics.test.ts
git commit -m "feat: add drafts and metrics pages"
```

## Task 11: Add Settings Page For Brand And CTA Defaults

**Files:**
- Create: `src/app/settings/page.tsx`

- [ ] **Step 1: Create settings page**

`src/app/settings/page.tsx`:

```tsx
import { AppNav } from "@/components/AppNav";

export default function SettingsPage() {
  return (
    <>
      <AppNav />
      <main className="max-w-3xl p-6">
        <h1 className="text-2xl font-bold">企业设置</h1>
        <section className="mt-6 rounded border bg-white p-4">
          <label className="block text-sm font-medium">目标读者</label>
          <input className="mt-1 w-full rounded border p-2" defaultValue="小微企业老板" />

          <label className="mt-4 block text-sm font-medium">品牌口吻</label>
          <input className="mt-1 w-full rounded border p-2" defaultValue="稳健、直接、面向老板" />

          <label className="mt-4 block text-sm font-medium">默认视觉风格</label>
          <input className="mt-1 w-full rounded border p-2" defaultValue="商务科技感" />

          <label className="mt-4 block text-sm font-medium">私信关键词</label>
          <input className="mt-1 w-full rounded border p-2" defaultValue="AI记账" />

          <label className="mt-4 block text-sm font-medium">默认私信回复</label>
          <textarea
            className="mt-1 min-h-28 w-full rounded border p-2"
            defaultValue="收到，欢迎关注 AI 记账和企业降本。请留下你的行业和企业规模，我们会邀请你进群交流。"
          />
        </section>
      </main>
    </>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `npm run build`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/app/settings/page.tsx
git commit -m "feat: add brand and cta settings page"
```

## Task 12: Add End-To-End MVP Flow Test

**Files:**
- Create: `tests/e2e/mvp-flow.spec.ts`

- [ ] **Step 1: Write e2e test**

`tests/e2e/mvp-flow.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

test("user can move from topic selection to article package and review metrics", async ({ page }) => {
  await page.goto("/topics");
  await expect(page.getByRole("heading", { name: "热点选题" })).toBeVisible();
  await expect(page.getByText("推荐热点选题")).toBeVisible();

  await page.getByRole("link", { name: /查看热点详情并进入结构仿写/ }).first().click();
  await expect(page.getByRole("heading", { name: "公众号图文素材包" })).toBeVisible();
  await expect(page.getByText("封面图与正文配图")).toBeVisible();
  await expect(page.getByText("风险提示")).toBeVisible();

  await page.goto("/drafts");
  await expect(page.getByRole("heading", { name: "草稿排期" })).toBeVisible();

  await page.goto("/metrics");
  await expect(page.getByRole("heading", { name: "数据复盘" })).toBeVisible();
  await expect(page.getByText("私信到进群转化")).toBeVisible();
});
```

- [ ] **Step 2: Run e2e test**

Run: `npm run test:e2e -- tests/e2e/mvp-flow.spec.ts`

Expected: PASS.

- [ ] **Step 3: Run full verification**

Run: `npm run test`

Expected: PASS.

Run: `npm run build`

Expected: PASS.

Run: `npm run test:e2e`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/mvp-flow.spec.ts
git commit -m "test: add mvp workspace flow"
```

## Task 13: Product Review And Scope Check

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Create README**

`README.md`:

````md
# AI 内容增长工作平台 MVP

这是一个内部 MVP，用于验证公众号图文内容生产链路：

1. 推荐热点或自然语言输入生成选题
2. 参考爆款结构，不复制原文
3. 生成公众号标题、摘要、正文、CTA、私信话术
4. 基于标题和正文生成封面图与正文配图提示
5. 保存草稿、排期、人工录入复盘数据

## 运行

```bash
npm install
npm run db:push
npm run db:seed
npm run dev
```

## 验证

```bash
npm run test
npm run build
npm run test:e2e
```

## V0.1 不做

- 不自动发布到公众号后台
- 不接平台授权
- 不大规模抓取公开内容库
- 不同时深做小红书和抖音
- 不承诺每篇内容都爆款
````

- [ ] **Step 2: Verify README commands**

Run: `npm run test`

Expected: PASS.

Run: `npm run build`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document mvp workflow"
```

## Self-Review Checklist

- Spec coverage:
  - Hotspot recommendation is covered by Tasks 2, 6, and 8.
  - Natural-language topic recommendation is covered by Tasks 2, 6, and 8.
  - Boom-article structure reference is covered by Task 3.
  - WeChat article package generation is covered by Tasks 4 and 9.
  - Cover image and inline image prompt generation is covered by Tasks 4 and 9.
  - CTA and private-message group invitation are covered by Tasks 4 and 9.
  - Draft and schedule workflow is covered by Tasks 5, 6, and 10.
  - Manual metrics review is covered by Tasks 6 and 10.
  - Brand visual defaults are covered by Tasks 5 and 11.
  - E2E flow is covered by Task 12.

- Scope boundaries:
  - The MVP uses deterministic generators instead of live model calls.
  - The MVP generates image prompts and review panels; real image generation can be wired behind the same `visuals.ts` interface after the prototype flow is validated.
  - The MVP does not auto-publish to WeChat.
  - The MVP does not scrape public content libraries.
  - The MVP does not build Xiaohongshu or Douyin creation surfaces.

- Required verification before handoff:
  - `npm run test`
  - `npm run build`
  - `npm run test:e2e`
