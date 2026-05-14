/**
 * js/components/graph-hud.js
 *
 * 职责：图谱 HUD — GraphLegend + GraphMetricsPanel + 内部子组件（LegendSwatch/MetricRow/MetricSub）
 * 依赖：__viz.NV, __viz.DomTags, __viz.state
 * 注册：__viz.comp.GraphLegend, __viz.comp.GraphMetricsPanel
 */
(function () {
  'use strict';

  const { ref } = window.__viz.NV;
  const { div, span, button } = window.__viz.DomTags;
  const state = window.__viz.state;

  const HUD_CLS = 'absolute top-3 left-3 flex flex-wrap items-center gap-3.5 bg-surface-1/85 backdrop-blur-sm border border-border rounded-md px-3 py-2 text-[11px] text-text-2';
  const LEG_CLS = 'inline-flex items-center gap-1.5';
  const SW_DOT = 'inline-block w-2.5 h-2.5 rounded-full';
  const SW_BAR = 'inline-block w-3.5 h-0.5 rounded-none';
  const K_CLS = 'text-text-3';
  const V_CLS = 'text-text-0 font-semibold tabular-nums';
  const SEP_CLS = 'text-text-3 mx-1';

  function LegendSwatch(color, label, opts) {
    const o = opts || {}; const style = { background: color };
    if (o.opacity != null) style.opacity = String(o.opacity);
    if (o.glow) style.boxShadow = '0 0 6px ' + color;
    if (o.dashed) { style.background = 'linear-gradient(90deg,' + color + ' 50%,transparent 50%)'; style.backgroundSize = '4px 2px'; }
    return span().class(LEG_CLS)(span().class((o.bar || o.dashed) ? SW_BAR : SW_DOT).style(style)(), span()(label));
  }

  function GraphLegend() {
    const nodesText = () => {
      void state.graph.version;
      return String(state.graph.nodes.filter(n => !n.removing).length);
    };
    const edgesText = () => {
      void state.graph.version;
      return String(state.graph.edges.filter(e => !e.removing).length);
    };
    return div().class(HUD_CLS)(
      span()(span().class(K_CLS)('nodes '), span().class(V_CLS)(nodesText)),
      span()(span().class(K_CLS)('edges '), span().class(V_CLS)(edgesText)),
      LegendSwatch('#58a6ff', 'active'),
      LegendSwatch('#d29922', 'stale'),
      LegendSwatch('#6e7681', 'superseded', { opacity: 0.5 }),
      LegendSwatch('#a371f7', 'matched'),
      LegendSwatch('#fbbf24', 'search pulse', { glow: true }),
      span().class(SEP_CLS)('|'),
      LegendSwatch('#7d8693', 'related', { bar: true }),
      LegendSwatch('#f85149', 'contradicts', { bar: true }),
      LegendSwatch('#a371f7', 'supersedes', { dashed: true }),
    );
  }

  // ---- GraphMetricsPanel helpers ----
  const PANEL_BASE = 'graph-panel absolute top-3 right-3 w-60 z-[5] bg-surface-0/85 backdrop-blur-md border border-border rounded-lg px-3.5 pt-3 pb-2.5 text-text-1 text-[11.5px] shadow-[0_6px_20px_rgba(0,0,0,0.4),0_0_20px_rgba(124,58,237,0.08)] transition-[width,padding] duration-200';
  const ROW_CLS = 'gp-row grid grid-cols-[20px_1fr_auto] items-center gap-1.5 py-1 border-b border-dashed border-border/35 last:border-b-0';
  const ICON_CLS = 'gp-icon text-[12px] opacity-90';
  const LABEL_CLS = 'gp-label text-text-2 text-[11px]';
  const VALUE_CLS = 'gp-value text-text-0 font-bold tabular-nums text-right min-w-[48px] transition-colors duration-500';
  const SUB_CLS = 'gp-sub col-span-2 col-start-2 text-[10px] text-text-3 -mt-0.5 mb-0.5';
  const HEAD_CLS = 'gp-head flex items-center justify-between mb-2.5 text-[10.5px] uppercase tracking-[1px] text-text-3';
  const TITLE_CLS = 'gp-title font-bold';
  const TOGGLE_CLS = 'gp-toggle w-5 h-5 rounded border border-border bg-transparent text-text-2 inline-flex items-center justify-center text-[12px] leading-none cursor-pointer transition-transform duration-200 hover:text-text-0 hover:bg-surface-3';
  const DIVIDER_CLS = 'gp-divider mt-2 mb-1.5 pt-1.5 border-t border-border text-[9.5px] uppercase tracking-[1px] text-text-3';
  const FILL_CLS = 'gp-fill flex h-1 rounded-sm bg-surface-3 overflow-hidden mt-0.5';
  const EMPTY_CLS = 'gp-empty text-[10.5px] text-text-3 text-center py-1';
  const COLL_ICON = 'gp-collapsed-icon text-[14px] text-text-1 text-center';
  const TOOL_CLS = 'gp-tool grid grid-cols-[60px_1fr_34px] items-center gap-1.5 text-[10.5px] py-0.5';
  const TOOL_NAME = 'gpt-name text-text-2 overflow-hidden text-ellipsis whitespace-nowrap';
  const TOOL_TRACK = 'gpt-track h-[5px] bg-surface-3 rounded-sm overflow-hidden';
  const TOOL_FILL = 'gpt-fill h-full rounded-sm bg-gradient-to-r from-brand-blue/80 to-brand-blue transition-[width] duration-500 ease-out';
  const TOOL_VAL = 'gpt-val text-right text-text-0 font-semibold tabular-nums';

  function MetricRow(icon, label, valueGetter, extraStyle) {
    const valEl = span().class(VALUE_CLS);
    if (extraStyle) valEl.style(extraStyle);
    return div().class(ROW_CLS)(
      span().class(ICON_CLS)(icon),
      span().class(LABEL_CLS)(label),
      valEl(valueGetter),
    );
  }

  function MetricSub(htmlBuilder) {
    return div().class(ROW_CLS)(
      span().class(ICON_CLS)(''),
      span().class(SUB_CLS)(htmlBuilder),
      span()(''),
    );
  }

  function GraphMetricsPanel() {
    const collapsed = ref(false);

    const S = () => state.stats || {};
    const total = () => (S().knowledge && S().knowledge.total) || 0;
    const active = () => (S().knowledge && S().knowledge.by_status && S().knowledge.by_status.active) || 0;
    const stale = () => (S().knowledge && S().knowledge.by_status && S().knowledge.by_status.stale) || 0;
    const searchCount = () => (S().tool_calls && S().tool_calls.search) || 0;
    const fbTotal = () => (S().feedback && S().feedback.total) || 0;
    const helpful = () => (S().feedback && S().feedback.by_signal && S().feedback.by_signal.helpful) || 0;
    const helpfulPct = () => { const t = fbTotal(); return t ? Math.round((helpful() / t) * 100) : 0; };
    const contradictions = () => (S().relations && S().relations.contradictions) || 0;
    const relationsByType = () => (S().relations && S().relations.by_type) || {};
    const relationsTotal = () => Object.values(relationsByType()).reduce((aa, bb) => aa + (bb || 0), 0);
    const toggle = () => { collapsed.value = !collapsed.value; };

    const toolsView = () => {
      const tc = (state.stats && state.stats.tool_calls) || {};
      const entries = Object.entries(tc).sort((aa, bb) => bb[1] - aa[1]).slice(0, 5);
      if (!entries.length) {
        return div().class(EMPTY_CLS)('暂无 tool_call');
      }
      const max = entries[0][1] || 1;
      const rows = entries.map(([name, n]) => {
        const pct = (n / max) * 100;
        const shortName = name.length > 8 ? name.slice(0, 8) : name;
        return div().class(TOOL_CLS)(
          span().class(TOOL_NAME).title(name)(shortName),
          div().class(TOOL_TRACK)(
            div().class(TOOL_FILL).style({ width: pct.toFixed(1) + '%' })(),
          ),
          span().class(TOOL_VAL)(String(n)),
        );
      });
      return div().class('gp-tools-list')(rows);
    };

    const relBars = () => {
      const by = relationsByType();
      const total2 = Object.values(by).reduce((aa, bb) => aa + (bb || 0), 0) || 1;
      const related = ((by.related || 0) / total2) * 100;
      const contra  = ((by.contradicts || 0) / total2) * 100;
      const sup     = ((by.supersedes || 0) / total2) * 100;
      const auto    = ((by.auto_related || 0) / total2) * 100;
      return div().class(FILL_CLS)(
        span().class('fill-related block h-full bg-brand-blue').style({ width: related.toFixed(1) + '%' })(),
        span().class('fill-contradicts block h-full bg-status-red').style({ width: contra.toFixed(1) + '%' })(),
        span().class('fill-supersedes block h-full bg-brand-purple').style({ width: sup.toFixed(1) + '%' })(),
        span().class('fill-auto block h-full bg-status-yellow').style({ width: auto.toFixed(1) + '%' })(),
      );
    };

    return div()
      .id('graph-panel')
      .class(() => PANEL_BASE + (collapsed.value ? ' collapsed' : ''))
      .onClick((e) => {
        if (collapsed.value) toggle();
      })(
        div().class(COLL_ICON).title('展开')('📊'),
        div().class(HEAD_CLS)(
          span().class(TITLE_CLS)('Live Metrics'),
          button()
            .class(TOGGLE_CLS)
            .title('折叠/展开')
            .onClick((e) => { e.stopPropagation(); toggle(); })(
              () => collapsed.value ? '+' : '−',
            ),
        ),
        div().class('gp-body')(
          MetricRow('📚', '知识', () => String(total())),
          MetricSub(() => {
            const a = active(); const s = stale();
            return span()(
              'active ',
              span().class('text-status-green font-semibold')(String(a)),
              ' · stale ',
              span().class('text-status-yellow font-semibold')(String(s)),
            );
          }),
          MetricRow('🔍', '搜索', () => String(searchCount())),
          MetricRow('👍', '反馈', () => String(fbTotal())),
          MetricSub(() => span()(
            span().class('text-status-green font-semibold')(String(helpfulPct())),
            '% helpful',
          )),
          MetricRow('⚡', '矛盾', () => String(contradictions()), { color: '#ffa198' }),
          MetricRow('🔗', '关系', () => String(relationsTotal())),
          div().class(ROW_CLS)(
            span().class(ICON_CLS)(''),
            span().class(SUB_CLS)(relBars),
            span()(''),
          ),
          div().class(DIVIDER_CLS)('Tool Calls'),
          div().id('gp-tools')(toolsView),
        ),
      );
  }

  // ---- 注册 ----
  window.__viz.comp = window.__viz.comp || {};
  window.__viz.comp.GraphLegend = GraphLegend;
  window.__viz.comp.GraphMetricsPanel = GraphMetricsPanel;
})();
