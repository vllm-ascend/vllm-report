# 前端增强方案（可选，后续迭代）

> 在现有 Web Dashboard 基础上，选择性利用新增数据增强功能。
> 所有改动向后兼容，不影响现有功能。

---

## 原则

1. **不破坏现有功能**：所有新增功能通过新增数据文件实现，不改动现有文件的格式
2. **渐进式增强**：每个功能独立，可按需逐个实现
3. **数据驱动**：前端只读数据展示，不做计算逻辑

---

## 增强一：适配状态标签

### 现状

vllm-ascend 的 commit 列表和 vllm 的 commit 列表展示方式一样，没有区分"哪些已经适配了"。

### 方案

前端读取 `data/vllm-ascend/adaptation-status.json`，在 commit 卡片上显示适配状态标签。

**commit 卡片变化：**

```
┌──────────────────────────────────────────────┐
│ [abc123] refactor attention backend interface │
│ 改动文件: 3  |  +120 -45                     │
│                                               │
│ [high-risk] [attention]                       │
│                                               │
│ ┌──────────────────────────────────────────┐  │
│ │ ⏳ 待适配  ·  影响 ascend               │  │
│ │ AscendAttentionBackend 需要适配新的      │  │
│ │ forward 签名                             │  │
│ └──────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

**状态标签样式：**

| 状态 | 标签 | 颜色 |
|------|------|------|
| `unknown` | ❓ 待确认 | 灰色 |
| `pending` | ⏳ 待适配 | 橙色 |
| `in_progress` | 🔄 适配中 | 蓝色 |
| `adapted` | ✅ 已适配 | 绿色 |
| `skipped` | ⏭️ 已跳过 | 灰色（透明度低） |

**实现方式：**

```javascript
// site/app.js 新增
async function loadAdaptationStatus() {
  if (currentRepo !== 'vllm-project/vllm-ascend') return null;
  const data = await fetchJSON(`${DATA_BASE}/vllm-ascend/adaptation-status.json`);
  if (!data) return null;
  // 构建 sha -> status 的映射
  const statusMap = {};
  data.commits.forEach(c => {
    statusMap[c.sha] = c;
  });
  return statusMap;
}
```

### 数据流

```
adaptation-status.json ──→ app.js（页面加载时读取）──→ commit 卡片渲染状态标签
```

### 交互

- 点击状态标签，显示该 commit 的适配详情（适配人、时间、备注）
- 状态标签可以作为 filter chip，筛选"只看待适配的 commit"

---

## 增强二：架构知识面板

### 现状

前端只展示 commit 列表和 diff，没有架构知识入口。

### 方案

在页面顶部 repo tabs 旁边新增一个"架构知识"按钮，点击后展开侧边面板。

**布局变化：**

```
┌──────────────────────────────────────────────────┐
│ [vllm] [vllm-ascend]  [📖 架构知识]             │
├──────────────────────────────────────────────────┤
│  commit 列表区域     │ 📖 架构知识面板（侧边栏） │
│                      │                           │
│  ...                 │ ## 核心模块               │
│                      │ - Attention Backends      │
│                      │ - Worker & Model Runner   │
│                      │ - ...                     │
│                      │                           │
│                      │ ## Patch 目录             │
│                      │ - platform (24)           │
│                      │ - worker (30)             │
│                      │                           │
│                      │ ## 测试指南               │
│                      │ - pytest -sv tests/ut/    │
└──────────────────────────────────────────────────┘
```

**数据来源：** `data/{repo}/context/architecture.json` 中的 `knowledge_base` 字段。

**展示内容（按 tab 切换）：**

| Tab | 数据来源 |
|-----|---------|
| 核心模块 | `arch.json.modules` |
| 关键抽象 | `arch.json.key_abstractions`（含文件路径和行号） |
| Patch 目录 | `arch.json.knowledge_base.patch_catalog` |
| 开发工作流 | `arch.json.knowledge_base.development_workflows` |
| 测试指南 | `arch.json.knowledge_base.testing_guide` |
| 耦合点 | `arch.json.cross_project_relationship` |

### 交互

- 点击 patch 列表中的条目，自动跳转到对应的 commit 搜索结果
- 点击模块名，按该模块过滤 commit 列表
- 测试指南中的命令可以一键复制

---

## 增强三：架构影响标记

### 现状

commit 卡片上没有标记"这个 commit 是否改变了架构接口"。

### 方案

前端读取 `data/{repo}/index.json` 中的 `architecture_impact_index`，在 commit 卡片上标记"架构影响"。

**commit 卡片变化：**

```
┌──────────────────────────────────────────────┐
│ [abc123] refactor attention backend interface │
│ 改动文件: 3  |  +120 -45                     │
│                                               │
│ ⚡ 架构影响：AttentionBackend 接口变更        │
│ [high-risk] [attention]                       │
└──────────────────────────────────────────────┘
```

**数据来源：** `data/{repo}/index.json` 的 `architecture_impact_index`。

### 交互

- 点击"架构影响"标记，显示受影响的具体接口列表
- 可以在 filter chip 中筛选"仅显示有架构影响的 commit"

---

## 增强四：搜索加速

### 现状

前端现有的跨日搜索（`app.js` 中的 `crossDayResults`）遍历所有 analysis 文件，请求次数多。

### 方案

改为先读 `index.json` 的 `keyword_index` 和 `tags_index`（SHA 列表）定位到日期，再读具体 analysis 文件。

**当前实现：**
```javascript
// 遍历所有日期，逐个 fetch analysis 文件
for (const date of availableDates) {
  const data = await fetchJSON(dataUrl(repo, 'analysis', date));
  if (data) searchInAnalysis(data, query);}
```

**优化后：**
```javascript
// 先读 index.json 定位日期（SHA 列表）
const index = await fetchJSON(`${DATA_BASE}/${repoDir}/index.json`);
const matchedDates = findInIndex(index, query);  // 本地匹配
for (const date of matchedDates) {
  const data = await fetchJSON(dataUrl(repo, 'analysis', date));
  // 读取详情
}
```

### 效果

- 搜索从 O(N) 次请求降为 O(1) + O(M)次（N=总天数，M=匹配天数）
- 通常 M << N，搜索响应速度显著提升

---

## 增强五：基线信息展示（vllm-ascend 专属）

### 现状

vllm-ascend tab 下的页面没有任何基线信息。

### 方案

在 vllm-ascend 的页面顶部增加一个"适配进度"指示器。

**展示位置：** 在 repo tabs 下方，日期导航栏上方。

```
┌──────────────────────────────────────────────────┐
│ [vllm] [vllm-ascend]                             │
│                                                  │
│ � 适配进度                                      │
│  main 基线: d02df74 [Bugfix] Accept RFC 2397..│
│  release 基线: v0.26.0                          │
│  待适配: 6  |  已适配: 5  |  待确认: 1        │
├──────────────────────────────────────────────────┤
```

**数据来源：**
- 基线信息：`vllm-ascend/.github/vllm-main-verified.commit` 和 `vllm-ascend/.github/vllm-release-tag.commit`
- 适配状态：`data/vllm-ascend/adaptation-status.json` 的 `stats` 字段

**注意：** 基线信息在 vllm-ascend 项目中（`vllm-ascend/.github/vllm-main-verified.commit`），不在 vllm-report 的 `data/` 目录下。前端通过 GitHub Pages 部署时无法直接读取跨项目的文件。**方案：** 通过 GitHub API 读取，不生成快照文件，与整体设计的"唯一真实来源"原则保持一致。

```javascript
async function loadBaseline() {
  if (currentRepo !== 'vllm-project/vllm-ascend') return null;
  try {
    // 读取 main 基线 SHA
    const mainResp = await fetch(
      'https://api.github.com/repos/vllm-project/vllm-ascend/contents/.github/vllm-main-verified.commit'
    );
    // 读取 release 基线 tag
    const releaseResp = await fetch(
      'https://api.github.com/repos/vllm-project/vllm-ascend/contents/.github/vllm-release-tag.commit'
    );
    if (!mainResp.ok || !releaseResp.ok) return null;
    const mainData = await mainResp.json();
    const releaseData = await releaseResp.json();
    return {
      mainVerifiedSha: atob(mainData.content).trim(),
      releaseTag: atob(releaseData.content).trim()
    };
  } catch {
    return null;
  }
}
```

> **为什么不生成快照？** 整体设计中明确"唯一真实来源"原则，`vllm-main-verified.commit` 是权威的。如果 `build_index.py` 生成快照，就会存在两份数据不同步的风险。GitHub API 方式虽然多一次请求，但保证实时性。GitHub API 未认证时有限流（60 次/小时），但基线信息每次页面加载只请求一次，足够使用。

---

## 实现优先级

| 优先级 | 功能 | 依赖 | 工作量 | 状态 |
|--------|------|------|--------|------|
| P1 | 适配状态标签 | `adaptation-status.json` | 小（仅 app.js + style.css 改动） | ⏳ 待开发 |
| P2 | 搜索加速 | `index.json` | 小（仅 app.js 改动） | ⏳ 待开发 |
| P3 | 架构影响标记 | `index.json` | 小 | ⏳ 待开发 |
| P3 | 基线信息展示 | GitHub API（无需后端改动） | 小（仅 app.js 改动） | ⏳ 待开发 |
| P4 | 架构知识面板 | `arch.json.knowledge_base` | 大（新增侧边栏组件） | ⏳ 待开发 |

> **注意：** 后端数据均已就绪（`adaptation-status.json`、`index.json`、`architecture.json` 等），前端增强为可选迭代，后续按需实现。

---

## 涉及的文件变更

| 文件 | 变更 |
|------|------|
| `site/app.js` | 新增适配状态标签、搜索加速、架构影响标记、基线信息展示（GitHub API 读取） |
| `site/style.css` | 新增状态标签样式、架构影响标记样式、基线指示器样式 |
| `site/index.html` | 可选：新增架构知识面板的侧边栏 DOM 结构 |