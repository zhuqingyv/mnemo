/**
 * js/components/tool-chart.js
 *
 * 职责：ToolChart — 工具调用统计横向条形图（对齐设计稿 37.png）
 * 依赖：__viz.NV (effect), __viz.DomTags (div/span/h3), __viz.state, __viz.t
 * 注册：__viz.comp.ToolChart
 */
(function () {
  'use strict';

  const { effect } = window.__viz.NV;
  const { div, span, h3 } = window.__viz.DomTags;
  const state = window.__viz.state;
  const t = window.__viz.t || function (k) { return k; };

  // 9 个工具 —— 顺序 + 渐变色，与设计稿对齐
  const TOOLS = [
    { key: 'search',          grad: 'from-[#3ee0c9] to-[#58a6ff]' },
    { key: 'feedback_knowledge', grad: 'from-[#a371f7] to-[#ec4899]' },
    { key: 'create_knowledge',   grad: 'from-[#7c3aed] to-[#58a6ff]' },
    { key: 'get_knowledge',      grad: 'from-[#58a6ff] to-[#58a6ff]' },
    { key: 'update_knowledge',   grad: 'from-[#ec4899] to-[#ec4899]' },
    { key: 'archive_knowledge',  grad: 'from-[#4338ca] to-[#6366f1]' },
    { key: 'delete_knowledge',   grad: 'from-[#dc2626] to-[#f85149]' },
    { key: 'list_tags',          grad: 'from-[#06b6d4] to-[#22d3ee]' },
    { key: 'search_by_tag',      grad: 'from-[#10b981] to-[#3fb950]' },
  ];

  // X 轴刻度（0 / 500 / 1k / 1.5k / 2k / 2.5k），与条宽使用相同分母 MAX
  const TICKS = [0, 500, 1000, 1500, 2000, 2500];
  const MAX = 2500;

  function toolRow(def) {
    const valEl = span().class('text-[11px] text-text-1 tabular-nums w-12 text-right shrink-0')();
    const fillEl = div()
      .class('h-full rounded-sm bg-gradient-to-r ' + def.grad + ' transition-[width] duration-500 ease-out')();

    effect(function () {
      const n = Number(((state.stats && state.stats.tool_calls) || {})[def.key] || 0);
      const pct = Math.max(0, Math.min(100, (n / MAX) * 100));
      const fn = fillEl.el || fillEl;
      if (fn) fn.style.width = pct + '%';
      const vn = valEl.el || valEl;
      if (vn) vn.textContent = String(n);
    });

    return div().class('flex items-center gap-3 py-[3px]')(
      span().class('text-[11px] text-text-1 w-36 shrink-0 truncate').title(def.key)(t('tool.' + def.key)),
      div().class('flex-1 h-[10px] bg-surface-3/60 rounded-sm overflow-hidden')(fillEl),
      valEl,
    );
  }

  function xAxis() {
    return div().class('relative h-5 text-[10px] text-text-3 mt-2 ml-[9.5rem] mr-[3.5rem]')(
      span().class('absolute left-0')(String(TICKS[0])),
      span().class('absolute left-[20%] -translate-x-1/2')(String(TICKS[1])),
      span().class('absolute left-[40%] -translate-x-1/2')('1k'),
      span().class('absolute left-[60%] -translate-x-1/2')('1.5k'),
      span().class('absolute left-[80%] -translate-x-1/2')('2k'),
      span().class('absolute right-0')('2.5k'),
    );
  }

  function ToolChart() {
    return div().class(
      'bg-surface-1 border border-border-glow rounded-lg px-4 py-3.5 mb-7 shadow-glow-purple'
    )(
      div().class('flex items-center justify-between mb-1')(
        h3().class('text-[13px] font-bold text-text-0 tracking-wider')(t('tools.title')),
        span().class('text-[10.5px] text-text-2')(t('tools.subtitle') + ' ▾'),
      ),
      div().class('text-[11px] text-text-2 mb-2')(t('tools.call_counts')),
      div().class('flex flex-col')(...TOOLS.map(toolRow)),
      xAxis(),
    );
  }

  // ---- 注册到 __viz ----
  window.__viz.comp = window.__viz.comp || {};
  window.__viz.comp.ToolChart = ToolChart;
})();
