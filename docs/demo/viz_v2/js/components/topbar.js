/**
 * js/components/topbar.js
 *
 * 职责：Topbar 组件 — brand / online-pill / SearchBar / view-switch / refresh / last-update
 * 依赖：__viz.NV (Dom.img), __viz.DomTags (div/span/button), __viz.state,
 *       __viz.util.syncViewToUrl, __viz.loader.{loadStats,loadKnowledge,loadRelations},
 *       __viz.comp.SearchBar, __viz.t
 * 注册：__viz.comp.Topbar
 */
(function () {
  'use strict';

  const { div, span, button } = window.__viz.DomTags;
  const state = window.__viz.state;
  const t = window.__viz.t;

  const SWITCH_BASE = 'px-3 py-1 rounded-md text-xs font-medium transition select-none';
  const SWITCH_ACTIVE = 'bg-brand-blue/20 text-brand-blue shadow-[inset_0_0_0_1px_rgba(88,166,255,0.35)]';
  const SWITCH_IDLE = 'text-text-2 hover:text-text-0 hover:bg-surface-3';

  function Topbar() {
    const _syncViewToUrl = window.__viz.util.syncViewToUrl;
    const { loadStats, loadKnowledge, loadRelations } = window.__viz.loader;
    const SearchBar = window.__viz.comp.SearchBar;

    return div().class(
      'sticky top-0 z-50 h-[52px] flex items-center gap-4 pr-4 ' +
      'pl-[72px] bg-[#0c1020]/90 backdrop-blur-xl ' +
      'border-b border-[#1a1f3a]'
    )(
      // brand
      div().class('flex items-center gap-2 shrink-0')(
        window.__viz.NV.Dom.img()
          .class('w-6 h-6 drop-shadow-[0_0_8px_rgba(124,58,237,0.5)]')
          .src('/viz/logo.png')
          .alt('mnemo')(),
        span().class(
          'font-bold tracking-wide text-[15px] ' +
          'bg-gradient-to-r from-brand-blue via-brand-glow to-brand-purple ' +
          'bg-clip-text text-transparent'
        )('mnemo'),
        span().class('text-text-3 text-[11px] font-mono')('v1.2'),
        span().class('text-text-3 text-[11px] select-none')('›'),
      ),

      // online pill
      div().class(() =>
        'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] ' +
        'border shrink-0 ' +
        (state.serverOnline
          ? 'bg-status-green/15 text-status-green border-status-green/30'
          : 'bg-status-red/15 text-status-red border-status-red/30')
      )(
        span().class(() =>
          'inline-block w-1.5 h-1.5 rounded-full ' +
          (state.serverOnline ? 'bg-status-green shadow-[0_0_6px_rgba(63,185,80,0.8)]' : 'bg-status-red')
        )(),
        span()(() => state.serverOnline ? t('topbar.online') : t('topbar.offline')),
      ),

      // search
      SearchBar(),

      // refresh button
      button()
        .class(
          'inline-flex items-center gap-1.5 h-9 px-3 rounded-lg shrink-0 ' +
          'bg-surface-2 border border-border text-text-0 text-[13px] ' +
          'hover:bg-surface-3 hover:border-border-strong hover:shadow-glow-sm transition'
        )
        .title(t('topbar.refresh'))
        .onClick(() => { loadStats(); loadKnowledge(); loadRelations(); })(
          span().class('text-[13px]')('⟳'),
          span()(t('topbar.refresh')),
        ),

      // last update
      div().class('inline-flex items-center gap-1.5 shrink-0 text-[11px] text-text-2')(
        span()(t('topbar.last_update') + ':'),
        span().class('text-text-1')(t('topbar.just_now')),
        span().class('inline-block w-1.5 h-1.5 rounded-full bg-status-green shadow-[0_0_6px_rgba(63,185,80,0.8)]')(),
      ),
    );
  }

  // ---- 注册到 __viz ----
  window.__viz.comp = window.__viz.comp || {};
  window.__viz.comp.Topbar = Topbar;
})();
