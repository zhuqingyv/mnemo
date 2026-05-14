/**
 * js/utils.js
 *
 * 职责：通用纯函数工具（HTML 转义、时间格式化、字符串切片、搜索命中 ID 提取、URL 视图同步）
 * 依赖：__viz.state（仅 syncViewToUrl 读写 state.view）
 * 注册：__viz.util.{escapeHtml, formatTimeAgo, sliceIso, truncateTitle, syncViewToUrl,
 *                  parseHitIdsFromSummary}
 */
(function () {
  'use strict';

  const state = window.__viz.state;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatTimeAgo(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    const now = new Date();
    const diff = (now - d) / 1000;
    if (diff < 60)    return Math.floor(diff) + '秒前';
    if (diff < 3600)  return Math.floor(diff / 60) + '分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
    return Math.floor(diff / 86400) + '天前';
  }

  function sliceIso(s) {
    return (s || '').slice(0, 19);
  }

  function truncateTitle(s, maxLen) {
    if (s.length <= maxLen) return s;
    return s.slice(0, maxLen) + '…';
  }

  function parseHitIdsFromSummary(summary) {
    if (!summary) return [];
    const ids = [];
    const re = /\(id:\s*(\d+)\)/g;
    let m;
    while ((m = re.exec(summary)) !== null) {
      ids.push(parseInt(m[1], 10));
    }
    return ids;
  }

  function syncViewToUrl() {
    const u = new URL(location.href);
    if (state.view === 'list') { u.searchParams.delete('view'); }
    else { u.searchParams.set('view', state.view); }
    history.pushState(null, '', u.toString());
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz.util = {
    escapeHtml,
    formatTimeAgo,
    sliceIso,
    truncateTitle,
    parseHitIdsFromSummary,
    syncViewToUrl,
  };
})();
