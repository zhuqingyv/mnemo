/**
 * js/graph2d/color.js
 *
 * 职责：2D 图谱颜色工具 — 解析 hex / rgb(a) 到 [r,g,b]、颜色插值、提亮
 * 依赖：无
 * 注册：__viz.g2d.color.{parseColor, lerpColor, brightenColor}
 */
(function () {
  'use strict';

  function parseColor(c) {
    if (!c) return [88, 166, 255];
    if (c[0] === '#') {
      const hex = c.slice(1);
      if (hex.length === 3) {
        return [parseInt(hex[0] + hex[0], 16), parseInt(hex[1] + hex[1], 16), parseInt(hex[2] + hex[2], 16)];
      }
      return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
    }
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (m) {
      const parts = m[1].split(',').map(s => parseFloat(s.trim()));
      return [parts[0] | 0, parts[1] | 0, parts[2] | 0];
    }
    return [88, 166, 255];
  }

  function lerpColor(from, to, t) {
    const a = parseColor(from);
    const b = parseColor(to);
    return 'rgb(' +
      Math.round(a[0] + (b[0] - a[0]) * t) + ',' +
      Math.round(a[1] + (b[1] - a[1]) * t) + ',' +
      Math.round(a[2] + (b[2] - a[2]) * t) + ')';
  }

  function brightenColor(c, t) {
    const [r, g, b] = parseColor(c);
    const nr = Math.round(r + (255 - r) * t);
    const ng = Math.round(g + (255 - g) * t);
    const nb = Math.round(b + (255 - b) * t);
    return 'rgb(' + nr + ',' + ng + ',' + nb + ')';
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz.g2d = window.__viz.g2d || {};
  window.__viz.g2d.color = { parseColor, lerpColor, brightenColor };
})();
