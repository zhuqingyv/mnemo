/**
 * js/detail-actions.js
 *
 * 职责：详情面板动作 — openDetail / closeDetail + ESC/⌘K 全局键盘快捷键
 * 依赖：__viz.state, __viz.apiGet, __viz.API
 * 注册：__viz.detail = { openDetail, closeDetail }
 */
(function () {
  'use strict';

  const state = window.__viz.state;
  const { apiGet, API } = window.__viz;

  async function openDetail(id) {
    if (id == null) return;
    state.selectedId = id;
    state.selectedDetail = null;
    state.selectedError = null;
    try {
      const k = await apiGet(API + '/knowledge/' + id + '/detail');
      if (state.selectedId === id) {
        state.selectedDetail = k;
      }
    } catch (e) {
      if (state.selectedId === id) {
        state.selectedError = e && e.message ? e.message : String(e);
      }
    }
  }

  function closeDetail() {
    state.selectedId = null;
    state.selectedDetail = null;
    state.selectedError = null;
  }

  document.addEventListener('keydown', function (e) {
    // ESC: 优先关详情 → 否则清搜索
    if (e.key === 'Escape') {
      if (state.selectedId !== null) {
        closeDetail();
      } else if (state.searchQuery) {
        state.searchQuery = '';
        state.searchResults = null;
        state.searching = false;
        var searchInput = document.querySelector('.search-wrap input') || document.querySelector('input[type="text"]');
        if (searchInput) searchInput.value = '';
      }
    }
    // ⌘K / Ctrl+K: 聚焦搜索框
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      var searchInput2 = document.querySelector('.search-wrap input') || document.querySelector('input[type="text"]');
      if (searchInput2) searchInput2.focus();
    }
  });

  // ---- 注册到 __viz ----
  window.__viz.detail = {
    openDetail,
    closeDetail,
  };
})();
