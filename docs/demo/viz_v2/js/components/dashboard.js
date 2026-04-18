/**
 * js/components/dashboard.js
 *
 * 职责：Dashboard 组件 — 4 张 MetricCard（总知识数 / 搜索调用 / 反馈质量 / 矛盾对）
 * 依赖：__viz.NV (effect/Show), __viz.DomTags (div/span), __viz.state, __viz.comp.MetricCard, __viz.t
 * 注册：__viz.comp.Dashboard
 */
(function () {
  'use strict';

  const { effect, Show } = window.__viz.NV;
  const { div, span } = window.__viz.DomTags;
  const state = window.__viz.state;
  const MetricCard = window.__viz.comp.MetricCard;
  const t = (k) => (window.__viz.t ? window.__viz.t(k) : k);

  function bigNumber(getter) {
    var el = div().class('text-[34px] font-bold tabular-nums text-text-0 leading-none tracking-tight')();
    effect(function () {
      var n = el.el || el;
      if (n) n.textContent = String(Number(getter() || 0));
    });
    return el;
  }

  function subCell(label, color, getter) {
    var valEl = span().class('font-bold tabular-nums').style({ color: color })();
    effect(function () {
      var n = valEl.el || valEl;
      if (n) n.textContent = String(Number(getter() || 0));
    });
    return span().class('inline-flex items-center gap-1 text-[11px] text-text-2')(
      span()(label), ' ', valEl,
    );
  }

  function barRow(label, color, valGetter, totGetter) {
    var fillEl = div().class('h-full rounded-full transition-[width] duration-500 ease-out')
      .style({ background: color, boxShadow: '0 0 6px ' + color + '66' })();
    var bvEl = span().class('text-[10.5px] text-text-2 tabular-nums w-8 text-right')();
    effect(function () {
      var v = Number(valGetter() || 0);
      var tot = Number(totGetter() || 0);
      var pct = tot > 0 ? Math.max(0, Math.min(100, (v / tot) * 100)) : 0;
      var fn = fillEl.el || fillEl;
      if (fn) fn.style.width = pct + '%';
      var bn = bvEl.el || bvEl;
      if (bn) bn.textContent = String(v);
    });
    return div().class('flex items-center gap-2')(
      span().class('text-[10.5px] text-text-2 w-12 shrink-0')(label),
      div().class('flex-1 h-1.5 bg-surface-3/60 rounded-full overflow-hidden')(fillEl),
      bvEl,
    );
  }

  function sparkPlaceholder() {
    return div().class('mt-2 h-8 rounded-md bg-gradient-to-r from-brand-purple/10 via-brand-purple/20 to-brand-blue/10 border border-border/40')();
  }

  function Dashboard() {
    // -- getters --
    const gTotal      = () => (state.stats && state.stats.knowledge && state.stats.knowledge.total) || 0;
    const gActive     = () => (state.stats && state.stats.knowledge && state.stats.knowledge.by_status && state.stats.knowledge.by_status.active) || 0;
    const gStale      = () => (state.stats && state.stats.knowledge && state.stats.knowledge.by_status && state.stats.knowledge.by_status.stale) || 0;
    const gArchived   = () => (state.stats && state.stats.knowledge && state.stats.knowledge.by_status && state.stats.knowledge.by_status.archived) || 0;
    const gSuperseded = () => (state.stats && state.stats.knowledge && state.stats.knowledge.by_status && state.stats.knowledge.by_status.superseded) || 0;

    const gSearchCount = () => (state.stats && state.stats.tool_calls && state.stats.tool_calls.search) || 0;

    const gFbTotal    = () => (state.stats && state.stats.feedback && state.stats.feedback.total) || 0;
    const gHelpful    = () => (state.stats && state.stats.feedback && state.stats.feedback.by_signal && state.stats.feedback.by_signal.helpful) || 0;
    const gMisleading = () => (state.stats && state.stats.feedback && state.stats.feedback.by_signal && state.stats.feedback.by_signal.misleading) || 0;
    const gOutdated   = () => (state.stats && state.stats.feedback && state.stats.feedback.by_signal && state.stats.feedback.by_signal.outdated) || 0;

    const gContradictions = () => (state.stats && state.stats.relations && state.stats.relations.contradictions) || 0;

    return Show(() => !!state.stats, {
      when: () => div().class('grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-7')(
        // -- Card 1: 总知识数 + 3 bar rows --
        MetricCard({
          icon: '📚',
          title: t('dash.knowledge_total'),
          color: '#58a6ff',
          badge: '📊',
          children: [
            bigNumber(gTotal),
            div().class('flex flex-wrap gap-x-3 gap-y-1 pt-1')(
              subCell(t('dash.active'),     '#3fb950', gActive),
              subCell(t('dash.stale'),      '#d29922', gStale),
              subCell(t('dash.superseded'), '#a371f7', gSuperseded),
              subCell(t('dash.archived'),   '#6e7681', gArchived),
            ),
            div().class('pt-1 space-y-1.5')(
              barRow(t('dash.active'),   '#3fb950', gActive,  gTotal),
              barRow(t('dash.stale'),    '#d29922', gStale,   gTotal),
              barRow(t('dash.archived'), '#6e7681', () => gArchived() + gSuperseded(), gTotal),
            ),
          ],
        }),

        // -- Card 2: 搜索调用 + sparkline 占位 --
        MetricCard({
          icon: '🔍',
          title: t('dash.search_calls'),
          color: '#a371f7',
          badge: '⚡',
          children: [
            bigNumber(gSearchCount),
            div().class('text-[11px] text-text-2 pt-0.5')(t('status.tool_search')),
            sparkPlaceholder(),
          ],
        }),

        // -- Card 3: 反馈质量 + 大数字旁括号百分比
        (function() {
          var pctEl = span().class('text-[16px] font-semibold text-status-green ml-2')();
          effect(function() {
            var total = gFbTotal();
            var helpful = gHelpful();
            var pct = total > 0 ? Math.round((helpful / total) * 100) : 0;
            var node = pctEl.el || pctEl;
            if (node) node.textContent = '(' + pct + '% ' + t('dash.helpful') + ')';
          });
          return MetricCard({
            icon: '👍',
            title: t('dash.feedback_quality'),
            color: '#3fb950',
            badge: '✨',
            children: [
              div().class('flex items-baseline gap-1')(
                bigNumber(gFbTotal),
                pctEl,
              ),
            div().class('flex flex-wrap gap-x-3 gap-y-1 pt-1')(
              subCell(t('dash.helpful'),    '#3fb950', gHelpful),
              subCell(t('dash.misleading'), '#f85149', gMisleading),
              subCell(t('dash.outdated'),   '#d29922', gOutdated),
            ),
            div().class('pt-1 space-y-1.5')(
              barRow(t('dash.helpful'),    '#3fb950', gHelpful,    gFbTotal),
              barRow(t('dash.misleading'), '#f85149', gMisleading, gFbTotal),
              barRow(t('dash.outdated'),   '#d29922', gOutdated,   gFbTotal),
            ),
          ],
        });
        })(),

        // -- Card 4: 矛盾对 --
        MetricCard({
          icon: '⚡',
          title: t('dash.contradictions'),
          color: '#f85149',
          badge: '⚠️',
          children: [
            bigNumber(gContradictions),
            div().class('text-[11px] text-text-2 pt-0.5')(t('status.relation_contradicts')),
          ],
        }),
      ),
      fallback: () => div().class('grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-7')(
        MetricCard({
          icon: '⏳', title: 'loading…', color: '#6e7681', badge: '·',
          children: [div().class('text-[34px] font-bold text-text-0 leading-none')('—')],
        }),
      ),
    });
  }

  window.__viz.comp = window.__viz.comp || {};
  window.__viz.comp.Dashboard = Dashboard;
})();
