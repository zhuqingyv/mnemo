/**
 * js/components/search-bar.js
 *
 * 职责：独立搜索框组件 — icon / input / ⌘K hint / clear button
 * 依赖：__viz.NV (watchEffect), __viz.DomTags (div/span/button/input),
 *       __viz.state, __viz.search (onSearchInput/clearSearch), __viz.t
 * 注册：__viz.comp.SearchBar
 */
(function () {
  'use strict';

  const { watchEffect } = window.__viz.NV;
  const { div, span, button, input } = window.__viz.DomTags;
  const state = window.__viz.state;
  const t = window.__viz.t;

  function SearchBar() {
    const { onSearchInput, clearSearch } = window.__viz.search;

    const inputEl = input()
      .class(
        'w-full h-9 bg-surface-2/80 text-text-0 placeholder:text-text-2 ' +
        'border border-border rounded-lg pl-9 pr-20 text-[13px] outline-none ' +
        'font-mono transition focus:border-brand-blue focus:bg-surface-2 ' +
        'focus:shadow-glow-sm'
      )
      .type('text')
      .placeholder(t('topbar.search_placeholder'))
      .onInput((e) => onSearchInput(e.target.value));

    watchEffect(() => {
      const q = state.searchQuery;
      const node = inputEl.el;
      if (node && node.value !== q) node.value = q;
    });

    return div().class('relative flex-1 min-w-[260px] max-w-[560px]')(
      // search icon (left)
      span()
        .class(
          'absolute left-3 top-1/2 -translate-y-1/2 text-text-2 text-sm ' +
          'pointer-events-none select-none'
        )('⌕'),

      inputEl,

      // ⌘K hint badge (right, hidden when query non-empty)
      span()
        .class(() =>
          'absolute right-10 top-1/2 -translate-y-1/2 ' +
          'px-1.5 py-0.5 rounded-md text-[10px] font-mono ' +
          'bg-surface-3 text-text-2 border border-border ' +
          'pointer-events-none select-none transition ' +
          (state.searchQuery ? 'opacity-0' : 'opacity-100')
        )('⌘K'),

      // clear button (right, only when query non-empty)
      button()
        .class(() =>
          'absolute right-2 top-1/2 -translate-y-1/2 ' +
          'w-6 h-6 flex items-center justify-center rounded ' +
          'text-text-2 hover:text-text-0 hover:bg-surface-3 ' +
          'transition ' +
          (state.searchQuery ? 'opacity-100' : 'opacity-0 pointer-events-none')
        )
        .title('clear')
        .onClick(() => clearSearch())('×'),
    );
  }

  // ---- 注册到 __viz ----
  window.__viz.comp = window.__viz.comp || {};
  window.__viz.comp.SearchBar = SearchBar;
})();
