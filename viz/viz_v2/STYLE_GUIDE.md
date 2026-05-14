# mnemo viz_v2 — Tailwind Style Guide

对齐 `assets/logo.png` 的视觉语言：**glow · gradient · glass-morphism · dark**。
目标：让 viz_v2 从「GitHub 暗色仪表盘」升级为「霓虹深空 M」感知。

实现方式：Tailwind Play CDN（零构建）+ 少量 `<style>` 块放 Tailwind 无法覆盖的部分。

---

## 1. LOGO 视觉分析

- **主体**：字母 M，玻璃质感描边，内部霓虹辉光
- **渐变**：左蓝 `#58a6ff` → 中紫 `#7c3aed` → 右品紫 `#a371f7`
- **发光**：双层 glow（内层密 20px 蓝，外层散 40px 紫）
- **背景**：近黑 `#0a0e14`（比 `#000` 略青）
- **装饰**：底部星尘粒子（`#a371f7` 半透明小点）

**关键词**：`glow / gradient / glass / dark / neon / stardust`

---

## 2. 色板（Tailwind theme）

### 2.1 surface（容器）
| Token | Hex | 用途 |
|---|---|---|
| `surface-0` | `#0a0e14` | body / 全屏 canvas 背景 |
| `surface-1` | `#111620` | topbar / card / panel 基础面 |
| `surface-2` | `#1a2030` | input / badge 底 |
| `surface-3` | `#252d3d` | hover / active 态 |

### 2.2 brand（LOGO 核心）
| Token | Hex | 用途 |
|---|---|---|
| `brand-blue` | `#58a6ff` | primary accent、链接、高亮 |
| `brand-glow` | `#7c3aed` | glow 中间过渡色 |
| `brand-purple` | `#a371f7` | secondary accent、supersedes |

### 2.3 border
| Token | Hex | 用途 |
|---|---|---|
| `border-DEFAULT` | `#2a3545` | 卡片/面板常规边 |
| `border-strong` | `#3a4560` | hover 强调边 |

### 2.4 text
| Token | Hex | 用途 |
|---|---|---|
| `text-0` | `#e6edf3` | 主文字 |
| `text-1` | `#b9c2cd` | 次文字 / 描述 |
| `text-2` | `#7d8693` | label / hint |
| `text-3` | `#565d68` | 弱提示 / 分隔 |

### 2.5 status
| Token | Hex | 用途 |
|---|---|---|
| `status-green` | `#3fb950` | ok / active / helpful |
| `status-yellow`| `#d29922` | warn / stale / outdated |
| `status-red` | `#f85149` | err / misleading / contradicts |
| `status-gray` | `#6e7681` | session / idle |

### 2.6 gradient（utility）
- `bg-gradient-brand`：`linear-gradient(90deg, #58a6ff 0%, #7c3aed 50%, #a371f7 100%)`
- `bg-gradient-brand-soft`：同上 + alpha 0.15（用于按钮底发光）
- 垂直变体：`bg-gradient-brand-v`（用于 accent bar）

---

## 3. Tailwind 组件样式规范

下面的 class 组合是推荐配方。组件文件（`js/components/*.js`）重写时对照使用。

### 3.1 Topbar
```
class="sticky top-0 z-50 h-[52px] flex items-center gap-3 px-4
       bg-surface-1/80 backdrop-blur-md border-b border-border
       shadow-[0_1px_0_rgba(124,58,237,0.15)]"
```

### 3.2 Brand (logo + 文字)
```
<div class="flex items-center gap-2">
  <img src="favicon.png" class="w-6 h-6 drop-shadow-[0_0_8px_rgba(124,58,237,0.6)]">
  <span class="font-bold tracking-wide
               bg-gradient-to-r from-brand-blue via-brand-glow to-brand-purple
               bg-clip-text text-transparent">mnemo</span>
  <span class="text-text-3 text-[11px]">/viz · v2</span>
</div>
```

### 3.3 Search input
```
class="w-full bg-surface-2 text-text-0 border border-border rounded-md
       pl-8 pr-9 py-2 text-[13px] outline-none font-mono
       focus:border-brand-blue focus:shadow-glow-sm"
```

### 3.4 Button (primary)
```
class="px-3 py-1.5 rounded-md bg-surface-2 border border-border text-text-0
       hover:bg-surface-3 hover:border-border-strong
       hover:shadow-glow-sm transition"
```

### 3.5 View switch (segmented)
容器：`inline-flex bg-surface-2 border border-border rounded-md p-0.5 gap-0.5`
按钮默认：`px-3 py-1 rounded text-text-2 text-xs hover:text-text-0`
激活：`bg-surface-3 text-text-0 shadow-[inset_0_0_0_1px_theme(colors.border.strong)]`

### 3.6 Metric card
```
class="relative overflow-hidden bg-surface-1 border border-border
       rounded-lg px-4 py-3.5
       hover:border-brand-blue/40 hover:shadow-glow-sm transition"
```
accent bar（左侧 3px）：
```
class="absolute top-0 left-0 bottom-0 w-[3px]
       bg-gradient-to-b from-brand-blue to-brand-purple"
```
（status 变体把 gradient 换成单色 `bg-status-green` 等。）

### 3.7 Knowledge card
```
class="relative overflow-hidden bg-surface-1 border border-border
       rounded-md px-3.5 py-3 cursor-pointer
       hover:border-brand-blue/50 hover:shadow-glow-sm
       hover:-translate-y-0.5 transition"
```
scope-bar（与 metric 同，3px 左侧条）。

### 3.8 Pill / Badge
基础：`inline-flex items-center px-1.5 py-0.5 rounded-full text-[9.5px]
       font-semibold uppercase tracking-wider border`
变体：
- `active`：`text-status-green bg-status-green/10 border-status-green/30`
- `stale`：`text-status-yellow bg-status-yellow/15 border-status-yellow/35`
- `archived/superseded`：`text-text-2 bg-white/5 border-border`
- `type`：`text-text-1 bg-surface-3 border-transparent`
- `scope-global`：`text-brand-blue bg-brand-blue/10 border-brand-blue/30`
- `scope-project`：`text-status-green bg-status-green/8 border-status-green/30`
- `scope-session`：`text-text-2 bg-white/5 border-border`

### 3.9 Detail panel (右侧抽屉)
overlay：`fixed inset-0 bg-black/50 z-[80] flex items-start justify-end`（初始 `hidden`，`show` 时 `flex`）
panel：
```
class="relative w-[460px] max-w-full h-screen overflow-y-auto
       bg-surface-1 border-l border-border px-5 py-4
       shadow-[-8px_0_32px_rgba(124,58,237,0.15)]
       animate-slide-in"
```

### 3.10 Graph HUD (左上)
```
class="absolute top-3 left-3 flex flex-wrap items-center gap-3.5
       bg-surface-1/85 backdrop-blur-sm border border-border
       rounded-md px-3 py-2 text-[11px] text-text-2"
```

### 3.11 Graph panel (右上浮动)
```
class="absolute top-3 right-3 w-60 z-[5]
       bg-surface-0/85 backdrop-blur-md border border-border
       rounded-lg px-3.5 pt-3 pb-2.5
       shadow-[0_6px_20px_rgba(0,0,0,0.4),0_0_20px_rgba(124,58,237,0.08)]
       transition-[width,padding] duration-200"
```

### 3.12 Tooltip
```
class="absolute pointer-events-none z-10 hidden
       bg-surface-1 border border-border-strong rounded px-2.5 py-1.5
       text-[11px] text-text-0 max-w-[320px] leading-snug
       shadow-[0_4px_14px_rgba(0,0,0,0.5)]"
```

### 3.13 Feedback pill
```
class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full
       text-[11.5px] bg-surface-2 border border-border"
```
变体同 3.8 的 active/stale/misleading，只是圆角 full。

### 3.14 Bar track / fill（进度条通用）
track：`h-1.5 bg-surface-3 rounded-sm overflow-hidden`
fill：`h-full rounded-sm bg-gradient-to-r from-brand-blue to-brand-purple transition-[width] duration-500 ease-out`

---

## 4. 排版

- **字体栈**：`font-mono` = `JetBrains Mono, Fira Code, SF Mono, Menlo, Consolas, monospace`（全局默认）
- **base size**：`text-[13px]`（body）
- **scale**（保留当前层次，方便组件对齐）：
  - metric 数值：`text-[28px] font-bold tabular-nums`
  - 卡片标题：`text-[13px] font-bold`
  - 卡片摘要：`text-[11.5px]`
  - pill：`text-[9.5px] uppercase tracking-wider`
  - hint / footer：`text-[10.5px]` 或 `text-[11px]`

数字统一加 `tabular-nums` 避免跳动。

---

## 5. 间距 / 布局

采用 Tailwind 默认 4px 基准：
- 卡片 padding：`px-3.5 py-3`（~14px/12px）
- 面板 padding：`px-5 py-4`
- grid gap：`gap-3`（12px）
- section 间距：`mb-7`（28px）
- list-wrap：`max-w-[1280px] mx-auto px-6 py-6 pb-12`
- topbar 高度固定 `h-[52px]`；graph 容器 `top-[52px]`

---

## 6. 发光 / 阴影（Tailwind extend）

```js
boxShadow: {
  glow:     '0 0 20px rgba(88,166,255,0.30), 0 0 40px rgba(163,113,247,0.20)',
  'glow-sm':'0 0 10px rgba(88,166,255,0.20)',
  'glow-lg':'0 0 30px rgba(88,166,255,0.35), 0 0 80px rgba(163,113,247,0.25)',
  'glow-purple':'0 0 18px rgba(163,113,247,0.35)',
},
dropShadow: {
  glow: ['0 0 8px rgba(88,166,255,0.6)', '0 0 16px rgba(163,113,247,0.4)'],
},
```

使用原则：
- 默认态**无 glow**（静态页面不闪）
- `hover/focus/active` 才加 `shadow-glow-sm`
- 仅 LOGO 文字 + 品牌主按钮用 `shadow-glow`

---

## 7. 动画保留清单

Tailwind 无法覆盖的部分，写到 `index.html` 的 `<style>` 块：

| CSS 块 | 原因 |
|---|---|
| `@keyframes spin` | spinner（list.css:110） |
| `@keyframes slide-in-panel` | detail panel 从右滑入（detail.css:19） |
| `@keyframes pulse-ring` | 搜索命中黄环（viz_v1 已有，v2 同步保留） |
| `#graph-view` / `#graph-3d-view` `fixed top:52px left:0 right:0 bottom:0` | Tailwind 位置可做，但 `.show` class 切 display 需配合 `display:none` 默认 — 仍用原生 CSS 最稳 |
| `#graph-3d-container` `width/height 100%` | 3d-force-graph 必须 wrapper 有显式尺寸 |
| `#label-layer` `pointer-events:none` + 绝对定位层 | 3D 标签层 |
| `.n-label` | 位置由 JS `transform` 写，Tailwind 不做 |
| `.graph-panel.collapsed` 等状态 class | JS 切换的状态样式 |
| 滚动条样式（可选） | `::-webkit-scrollbar` 细条深色 |

动画规范：
- `spin` 0.9s linear infinite
- `slide-in-panel` 220ms ease-out（translateX 20px → 0, opacity）
- `pulse-ring` 900ms ease-out（scale 0.6→1.8 + opacity 1→0）

---

## 8. 暗色模式

- Tailwind 配置 `darkMode: 'class'`
- `<html>` 永远加 `class="dark"`（viz_v2 只做暗色）
- 避免写 `dark:` 前缀，所有 token 已锁定暗色值
- LOGO 不做亮色变体（页面永远黑底）

---

## 9. 迁移映射速查表（旧 CSS var → Tailwind）

| 旧 var | Tailwind 替代 |
|---|---|
| `var(--bg-0)` | `bg-surface-0` |
| `var(--bg-1)` | `bg-surface-1` |
| `var(--bg-2)` | `bg-surface-2` |
| `var(--bg-3)` | `bg-surface-3` |
| `var(--border)` | `border-border` |
| `var(--border-strong)` | `border-border-strong` |
| `var(--text-0)` | `text-text-0` |
| `var(--text-1)` | `text-text-1` |
| `var(--text-2)` | `text-text-2` |
| `var(--text-3)` | `text-text-3` |
| `var(--accent)` | `text/bg-brand-blue` |
| `var(--accent-dim)` | `text/bg-brand-blue/80` 或 `#1f6feb` (inline) |
| `var(--purple)` | `text/bg-brand-purple` |
| `var(--green/yellow/red/gray)` | `text/bg-status-*` |
| `var(--mono)` | `font-mono` |
| `var(--radius)` | `rounded-md`（6px） |

---

## 10. 下一步（本任务之外）

1. 当前只改 `index.html`（加 Tailwind CDN + 删 link + 保留动画 style）。JS 组件仍输出旧 class，页面会"退化"为无样式状态——**这是预期**。
2. 后续步骤按 Wave 改 `js/components/*.js`，把 `class="..."` 替换为本指南 §3 的组合。
3. 组件全部替换完成后，删除 `css/` 目录与 `index.html.bak`。
