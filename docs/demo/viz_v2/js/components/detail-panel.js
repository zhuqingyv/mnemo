/**
 * js/components/detail-panel.js
 *
 * 职责：DetailPanel 组件 — 深紫霓虹风格右侧抽屉
 * 依赖：__viz.NV, __viz.DomTags, __viz.state, __viz.detail, __viz.util, __viz.t
 * 注册：__viz.comp.DetailPanel
 */
(function () {
  'use strict';

  const { component, Show, For } = window.__viz.NV;
  const { div, span, button, h3, h4, ul, li, a } = window.__viz.DomTags;
  const state = window.__viz.state;
  const { openDetail, closeDetail } = window.__viz.detail;
  const { formatTimeAgo, sliceIso } = window.__viz.util;
  const t = window.__viz.t || ((k) => k);

  const FB_BASE =
    'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11.5px] ' +
    'font-medium border backdrop-blur-sm transition';
  const FB_VARIANT = {
    helpful:    'bg-status-green/10 border-status-green/40 text-status-green shadow-[0_0_10px_rgba(34,197,94,0.25)]',
    misleading: 'bg-status-red/10 border-status-red/40 text-status-red shadow-[0_0_10px_rgba(239,68,68,0.25)]',
    outdated:   'bg-status-yellow/10 border-status-yellow/40 text-status-yellow shadow-[0_0_10px_rgba(234,179,8,0.25)]',
  };
  const REL_BADGE_BASE =
    'inline-flex items-center px-1.5 py-0.5 rounded-md text-[9.5px] ' +
    'font-semibold uppercase tracking-wider border';
  const REL_BADGE_VARIANT = {
    supersedes:  'text-brand-purple bg-brand-purple/15 border-brand-purple/40',
    contradicts: 'text-status-red bg-status-red/10 border-status-red/40',
    related:     'text-text-1 bg-surface-3 border-border/60',
  };

  function FeedbackBar(fb) {
    if (!fb || !fb.total) {
      return div().class('flex items-center gap-2 py-1')(
        span().class('text-[11.5px] text-text-3 italic')(t('detail.no_feedback'))
      );
    }
    return div().class('flex flex-wrap items-center gap-2 py-1')(
      div().class(FB_BASE + ' ' + FB_VARIANT.helpful)(
        span().class('font-bold tabular-nums')(String(fb.helpful || 0)), ' ', t('dash.helpful')
      ),
      div().class(FB_BASE + ' ' + FB_VARIANT.misleading)(
        span().class('font-bold tabular-nums')(String(fb.misleading || 0)), ' ', t('dash.misleading')
      ),
      div().class(FB_BASE + ' ' + FB_VARIANT.outdated)(
        span().class('font-bold tabular-nums')(String(fb.outdated || 0)), ' ', t('dash.outdated')
      )
    );
  }

  function RelItem(r) {
    const arrow = r.direction === 'outgoing' ? '→' : '←';
    const badge = r.relation_type || 'related';
    const badgeCls = REL_BADGE_VARIANT[badge] || REL_BADGE_VARIANT.related;
    const title = r.peer_title || ('#' + r.peer_id);
    const weight = (r.weight != null) ? ('w=' + r.weight) : '';

    return li().class('flex items-center gap-2 py-1.5 text-[12px]')(
      span().class('text-brand-purple/70 w-4 text-center text-[13px] font-mono')(arrow),
      span().class(REL_BADGE_BASE + ' ' + badgeCls)(badge),
      span()
        .class('flex-1 text-text-0 cursor-pointer hover:text-brand-blue hover:underline truncate transition')
        .onClick(() => openDetail(r.peer_id))(title),
      weight ? span().class('text-[10.5px] text-text-3 tabular-nums shrink-0')(weight) : ''
    );
  }

  function RelationList(relations) {
    if (!relations || relations.length === 0) {
      return div().class('text-[11.5px] text-text-3 italic py-1')(t('detail.no_relations'));
    }
    return ul().class('flex flex-col divide-y divide-border/40')(
      For(() => relations, {
        key: (r) => (r.peer_id != null ? r.peer_id : '') + ':' + (r.relation_type || '') + ':' + (r.direction || ''),
        children: (r) => RelItem(r),
      })
    );
  }

  function MetaRow(k, v) {
    return div().class('flex items-baseline gap-3 py-1 text-[11.5px]')(
      span().class('w-20 shrink-0 text-text-3 uppercase tracking-[0.12em] text-[10px] font-medium')(k),
      span().class('text-text-1 break-all flex-1')(v)
    );
  }

  function MetaBlock(k) {
    const rows = [
      MetaRow('id',      String(k.id)),
      MetaRow('status',  k.status || ''),
      MetaRow('scope',   k.scope || ''),
    ];
    if (k.claim_type)   rows.push(MetaRow('claim_type', k.claim_type));
    if (k.project_name) rows.push(MetaRow('project',    k.project_name));
    rows.push(MetaRow('version', 'v' + (k.version != null ? k.version : '?')));
    rows.push(MetaRow('created', sliceIso(k.created_at)));
    rows.push(MetaRow('updated', sliceIso(k.updated_at)));
    if (k.last_accessed_at) rows.push(MetaRow(t('detail.last_hit'), formatTimeAgo(k.last_accessed_at)));
    if (k.source)           rows.push(MetaRow('source',   k.source));
    const tags = (k.tags || []).map((tg) => '#' + tg).join(' ');
    rows.push(MetaRow('tags', tags));

    return div().class('bg-surface-2/50 rounded-xl p-4 my-4 border border-border/40')(rows);
  }

  function SupersededBanner(sb) {
    if (!sb) return null;
    return div().class(
      'flex items-center gap-1.5 px-3 py-2 my-3 rounded-lg ' +
      'bg-status-yellow/10 border border-status-yellow/40 ' +
      'shadow-[0_0_12px_rgba(234,179,8,0.2)] ' +
      'text-[11.5px] text-status-yellow'
    )(
      span()('⚠ ' + t('detail.superseded_by')),
      a().class('underline cursor-pointer hover:text-brand-blue transition')
        .onClick(() => openDetail(sb.id))(sb.title || ('#' + sb.id))
    );
  }

  function PanelBody() {
    if (state.selectedError) {
      return div().class(
        'px-3 py-2 my-2 rounded-lg bg-status-red/10 border border-status-red/40 ' +
        'shadow-[0_0_12px_rgba(239,68,68,0.2)] text-[12px] text-status-red'
      )('加载失败: ' + state.selectedError);
    }
    const k = state.selectedDetail;
    if (!k) {
      return div().class('text-text-3 text-[12px] py-8 text-center italic')(t('list.loading'));
    }

    const relations = k.relations || [];
    const h4Cls = 'mt-5 mb-2 text-[10px] font-semibold uppercase tracking-[0.15em] text-brand-purple/70';
    const contentBoxCls =
      'bg-surface-2/30 border border-border/50 rounded-lg p-3 ' +
      'text-[12px] text-text-1 leading-relaxed whitespace-pre-wrap break-words';

    return div()(
      h3().class('text-[18px] font-bold text-white leading-snug pr-10 tracking-tight')(k.title || ''),
      SupersededBanner(k.superseded_by),
      MetaBlock(k),
      h4().class(h4Cls)(t('detail.feedback')),
      FeedbackBar(k.feedback),
      h4().class(h4Cls)(t('detail.relations') + ' (' + relations.length + ')'),
      RelationList(relations),
      h4().class(h4Cls)(t('detail.summary')),
      div().class(contentBoxCls)(k.summary || ''),
      h4().class(h4Cls)(t('detail.content')),
      div().class(contentBoxCls)(k.content || '(no content)')
    );
  }

  const OVERLAY_BASE =
    'fixed inset-0 bg-black/60 backdrop-blur-sm z-[80] items-start justify-end';
  const PANEL_CLS =
    'relative w-[460px] max-w-full h-screen overflow-y-auto ' +
    'bg-[#0c1020] border-l border-[#1a1f3a] px-6 py-5 ' +
    'shadow-[-8px_0_40px_rgba(124,58,237,0.2)] animate-slide-in';
  const CLOSE_BTN_CLS =
    'absolute top-4 right-4 w-8 h-8 rounded-full flex items-center justify-center ' +
    'text-[14px] text-text-2 bg-surface-2/60 border border-border/60 ' +
    'hover:text-white hover:border-brand-purple/50 hover:bg-brand-purple/10 ' +
    'hover:shadow-[0_0_12px_rgba(124,58,237,0.4)] transition';

  const DetailPanel = component(() => {
    return div()
      .class(() => OVERLAY_BASE + ' ' + (state.selectedId !== null ? 'flex' : 'hidden'))
      .onClick((e) => {
        if (e.target === e.currentTarget) {
          closeDetail();
        }
      })(
        Show(() => state.selectedId !== null, {
          when: () => div().class(PANEL_CLS)(
            button().class(CLOSE_BTN_CLS).onClick(closeDetail)('✕'),
            () => PanelBody()
          ),
        })
      );
  });

  window.__viz.comp = window.__viz.comp || {};
  window.__viz.comp.DetailPanel = DetailPanel;
})();
