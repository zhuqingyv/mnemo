/**
 * js/components/metric-card.js
 *
 * 职责：通用指标卡片组件 — 深底 + 紫光边框 + 左上 emoji/标题 + 右上彩色圆形图标 + 数字/子指标槽位
 * 依赖：__viz.DomTags (div/span)
 * 注册：__viz.comp.MetricCard
 */
(function () {
  'use strict';

  const { div, span } = window.__viz.DomTags;

  const CARD_CLS =
    'relative bg-surface-1 border border-border/60 rounded-2xl p-5 ' +
    'hover:border-brand-glow/40 hover:shadow-glow-sm transition';

  function MetricCard(opts) {
    var icon   = (opts && opts.icon)   || '';
    var title  = (opts && opts.title)  || '';
    var color  = (opts && opts.color)  || '#7c3aed';
    var badge  = (opts && opts.badge)  || '';
    var kids   = (opts && opts.children) || [];

    var badgeEl = div()
      .class('absolute top-4 right-4 w-11 h-11 rounded-xl flex items-center justify-center text-[20px]')
      .style({
        background: 'linear-gradient(135deg,' + color + '33,' + color + '0d)',
        border: '1px solid ' + color + '55',
        boxShadow: '0 0 12px ' + color + '44',
        color: color,
      })(badge);

    var titleNode = (opts && opts.titleEl) || span()(title);
    var head = div().class('flex items-center gap-1.5 text-[11px] text-text-2 uppercase tracking-wider mb-2.5')(
      span().class('text-[13px]')(icon),
      titleNode,
    );

    return div().class(CARD_CLS)(
      badgeEl,
      head,
      div().class('space-y-2')(kids),
    );
  }

  window.__viz.comp = window.__viz.comp || {};
  window.__viz.comp.MetricCard = MetricCard;
})();
