/**
 * js/search.js
 *
 * 职责：搜索动作 — runSearch / onSearchInput (debounce 220ms) / clearSearch / triggerSearch
 * 依赖：__viz.state, __viz.apiGet, __viz.API
 * 注册：__viz.search = { runSearch, onSearchInput, clearSearch, triggerSearch }
 */
(function () {
  'use strict';

  const state = window.__viz.state;
  const { apiGet, API } = window.__viz;

  // -------- search action (debounced) --------
  let _searchDebounceTimer = null;

  function runSearch(q) {
    state.searching = true;
    apiGet(API + '/knowledge/search?query=' + encodeURIComponent(q) + '&limit=50')
      .then((data) => {
        // 只在仍处于同一个 query 时写回，避免快速输入时的过期结果覆盖新结果
        if (state.searchQuery === q) {
          state.searchResults = data.results || [];
        }
      })
      .catch(() => {
        if (state.searchQuery === q) {
          state.searchResults = [];
        }
      })
      .finally(() => {
        if (state.searchQuery === q) {
          state.searching = false;
        }
      });
  }

  function onSearchInput(v) {
    state.searchQuery = v;
    if (_searchDebounceTimer) {
      clearTimeout(_searchDebounceTimer);
      _searchDebounceTimer = null;
    }
    if (!v.trim()) {
      state.searchResults = null;
      state.searching = false;
      return;
    }
    _searchDebounceTimer = setTimeout(() => runSearch(v), 220);
  }

  function clearSearch() {
    state.searchQuery = '';
    state.searchResults = null;
    state.searching = false;
    if (_searchDebounceTimer) {
      clearTimeout(_searchDebounceTimer);
      _searchDebounceTimer = null;
    }
  }

  // Alias exposed to satisfy the contract from the merge brief.
  function triggerSearch(q) { onSearchInput(q == null ? '' : String(q)); }

  // ---- 注册到 __viz ----
  window.__viz.search = {
    runSearch,
    onSearchInput,
    clearSearch,
    triggerSearch,
  };
})();
