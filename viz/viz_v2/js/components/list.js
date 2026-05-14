/**
 * js/components/list.js
 *
 * 职责：KnowledgeList / KnowledgeCard 组件 — 对齐 viz_v2 设计稿（紫色 glow 卡片）
 * 依赖：__viz.NV (Show/For/effect), __viz.DomTags (div/span/button),
 *       __viz.state, __viz.detail.openDetail, __viz.t, __viz.util.sliceIso
 * 注册：__viz.comp.KnowledgeList, __viz.comp.KnowledgeCard
 */
(function () {
  'use strict';

  const { Show, For, effect } = window.__viz.NV;
  const { div, span, button } = window.__viz.DomTags;
  const state = window.__viz.state;
  const t = window.__viz.t;
  const sliceIso = (window.__viz.util && window.__viz.util.sliceIso) || function (s) { return String(s || '').slice(0, 10); };

  const TYPE_VARIANT = {
    fact:       'bg-brand-blue/10 text-brand-blue border-brand-blue/30',
    decision:   'bg-brand-purple/10 text-brand-purple border-brand-purple/30',
    procedure:  'bg-brand-purple/10 text-brand-purple border-brand-purple/30',
    hypothesis: 'bg-status-yellow/10 text-status-yellow border-status-yellow/30',
  };
  const STATUS_VARIANT = {
    active:     'bg-status-green/10 text-status-green border-status-green/30',
    stale:      'bg-status-yellow/10 text-status-yellow border-status-yellow/30',
    archived:   'bg-white/5 text-text-2 border-border',
    superseded: 'bg-white/5 text-text-2 border-border',
  };
  const BADGE_BASE =
    'inline-flex px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ' +
    'tracking-wider border';
  const TAG_CLS =
    'inline-flex items-center px-2 py-0.5 rounded-full text-[10.5px] ' +
    'text-brand-purple/90 bg-brand-purple/5 border border-brand-purple/25';
  const CARD_CLS =
    'bg-surface-1 border border-border/50 rounded-2xl p-5 cursor-pointer ' +
    'hover:border-brand-purple/40 hover:shadow-glow-purple transition-all duration-300 ' +
    'flex flex-col gap-3';

  function KnowledgeCard(k) {
    const openDetail = window.__viz.detail.openDetail;
    const status = k.status || 'active';
    const type = k.claim_type || '';
    const statusCls = STATUS_VARIANT[status] || STATUS_VARIANT.active;
    const typeCls = TYPE_VARIANT[type] || 'bg-white/5 text-text-2 border-border';

    const titleEl = div().class('text-[14px] font-bold text-text-0 leading-snug line-clamp-2')();
    effect(function () {
      const n = titleEl.el || titleEl;
      if (n) n.textContent = String(k.title || '(no title)');
    });

    const summaryEl = div().class('text-[12px] text-text-2 leading-relaxed line-clamp-3 flex-1')();
    effect(function () {
      const n = summaryEl.el || summaryEl;
      if (n) n.textContent = String(k.summary || '');
    });

    const dateEl = span().class('tabular-nums')();
    effect(function () {
      const n = dateEl.el || dateEl;
      if (n) n.textContent = sliceIso(k.updated_at || k.created_at || '');
    });

    const scopeEl = span().class('truncate max-w-[80px]')();
    effect(function () {
      const n = scopeEl.el || scopeEl;
      if (n) n.textContent = String(k.project_name || k.scope || 'global');
    });

    const topBadges = [
      span().class(BADGE_BASE + ' ' + statusCls)(String(status)),
    ];
    if (type) topBadges.push(span().class(BADGE_BASE + ' ' + typeCls)(String(type)));
    if (k._score != null) {
      topBadges.push(
        span().class('ml-auto text-[10px] text-text-3 tabular-nums')(
          'score ' + Number(k._score).toFixed(2),
        ),
      );
    }

    const tags = (k.tags || []).slice(0, 4).map(function (x) {
      return span().class(TAG_CLS)('#' + String(x));
    });
    if (k.version && k.version > 1) tags.push(span().class(TAG_CLS)('v' + k.version));

    const fav = button()
      .class(
        'text-text-3 hover:text-status-yellow transition-colors text-[13px] ' +
        'leading-none select-none',
      )
      .onClick(function (e) { e.stopPropagation(); })(
        'star',
      );

    return div()
      .class(CARD_CLS)
      .onClick(function () { openDetail(k.id); })(
        div().class('flex items-center gap-1.5 flex-wrap')(...topBadges),
        titleEl,
        summaryEl,
        tags.length
          ? div().class('flex flex-wrap gap-1.5')(...tags)
          : null,
        div().class('flex items-center gap-3 text-[11px] text-text-3 pt-2 border-t border-border/40')(
          span().class('inline-flex items-center gap-1')(span()('cal'), ' ', dateEl),
          span().class('inline-flex items-center gap-1')(span()('@'), ' ', scopeEl),
          span().class('ml-auto')(fav),
        ),
      );
  }

  function KnowledgeList() {
    const itemsGetter = function () {
      return state.searchResults !== null ? state.searchResults : state.knowledge;
    };

    const stateRowCls =
      'col-span-full flex items-center gap-2 text-text-2 text-[12px] py-10 justify-center';
    const spinnerCls =
      'inline-block w-3 h-3 border-2 border-border border-t-brand-purple ' +
      'rounded-full animate-[spin_0.9s_linear_infinite]';
    const gridCls =
      'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4';

    return Show(
      function () { return state.searching; },
      {
        when: function () {
          return div().class(gridCls)(
            div().class(stateRowCls)(span().class(spinnerCls)(), t('list.loading')),
          );
        },
        fallback: function () {
          return Show(
            function () {
              const items = itemsGetter();
              return !items || items.length === 0;
            },
            {
              when: function () {
                return Show(
                  function () { return state.loaded || state.searchResults !== null; },
                  {
                    when: function () {
                      return div().class(gridCls)(
                        div().class(stateRowCls)(t('list.no_results')),
                      );
                    },
                    fallback: function () {
                      return div().class(gridCls)(
                        div().class(stateRowCls)(span().class(spinnerCls)(), t('list.loading')),
                      );
                    },
                  },
                );
              },
              fallback: function () {
                return div().class(gridCls)(
                  For(function () { return itemsGetter(); }, {
                    key: function (k) { return k.id; },
                    children: function (k) { return KnowledgeCard(k); },
                  }),
                );
              },
            },
          );
        },
      },
    );
  }

  window.__viz.comp = window.__viz.comp || {};
  window.__viz.comp.KnowledgeList = KnowledgeList;
  window.__viz.comp.KnowledgeCard = KnowledgeCard;
})();
