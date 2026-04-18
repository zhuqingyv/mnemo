/**
 * js/graph2d/sprite-cache.js
 *
 * 职责：offscreen canvas 精灵缓存 — glow 发光贴图 + 文字标签贴图
 * 依赖：无
 * 注册：__viz.g2d.getGlowSprite, __viz.g2d.getLabelSprite
 */
(function () {
  'use strict';

  // ---- glow sprite ----
  const glowSpriteCache = new Map();
  function getGlowSprite(color, nodeRadius, blurStrength) {
    const key = color + '|' + nodeRadius + '|' + blurStrength;
    const hit = glowSpriteCache.get(key);
    if (hit) return hit;
    const pad = blurStrength * 2 + 4;
    const size = Math.ceil((nodeRadius + pad) * 2);
    const oc = document.createElement('canvas');
    oc.width = size;
    oc.height = size;
    const octx = oc.getContext('2d');
    const cx = size / 2;
    const cy = size / 2;
    const grad = octx.createRadialGradient(cx, cy, nodeRadius * 0.6, cx, cy, nodeRadius + blurStrength);
    grad.addColorStop(0, color);
    grad.addColorStop(0.5, color);
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    octx.fillStyle = grad;
    octx.beginPath();
    octx.arc(cx, cy, nodeRadius + blurStrength, 0, Math.PI * 2);
    octx.fill();
    const sprite = { canvas: oc, half: size / 2 };
    glowSpriteCache.set(key, sprite);
    return sprite;
  }

  // ---- label sprite ----
  // 把 10px 节点标签预渲染到 offscreen canvas，每帧 drawImage 替代 fillText。
  // drawImage 是纹理拷贝（GPU 路径），fillText 是全 CPU 光栅化；同一文本命中 cache 后接近零成本。
  const LABEL_FONT = '10px -apple-system, "SF Pro Text", monospace';
  const LABEL_FILL = '#b9c2cd';
  const LABEL_DPR = Math.min(2, window.devicePixelRatio || 1);
  const labelSpriteCache = new Map();
  const LABEL_CACHE_MAX = 2000; // 避免极端情况下无限增长

  // 一个共享的测量 ctx，ctx.font 只设一次
  const _measureCanvas = document.createElement('canvas');
  const _measureCtx = _measureCanvas.getContext('2d');
  _measureCtx.font = LABEL_FONT;

  function getLabelSprite(text) {
    const key = text;
    const hit = labelSpriteCache.get(key);
    if (hit) return hit;
    if (labelSpriteCache.size >= LABEL_CACHE_MAX) labelSpriteCache.clear(); // 简单回收
    const metrics = _measureCtx.measureText(text || '');
    const tw = Math.max(1, Math.ceil(metrics.width) + 2);
    const th = 14; // 10px 字体 + 上下留白
    const oc = document.createElement('canvas');
    oc.width = Math.max(1, Math.ceil(tw * LABEL_DPR));
    oc.height = Math.max(1, Math.ceil(th * LABEL_DPR));
    const octx = oc.getContext('2d');
    octx.scale(LABEL_DPR, LABEL_DPR);
    octx.font = LABEL_FONT;
    octx.fillStyle = LABEL_FILL;
    octx.textAlign = 'left';
    octx.textBaseline = 'middle';
    octx.fillText(text || '', 1, th / 2);
    // 绘制时 drawImage(sprite.canvas, x, y)，我们用逻辑尺寸；显式 drawImage 9 参数可更明确
    const sprite = {
      canvas: oc,
      w: tw,
      h: th,
      halfH: th / 2,
      dpr: LABEL_DPR,
    };
    labelSpriteCache.set(key, sprite);
    return sprite;
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz.g2d = window.__viz.g2d || {};
  window.__viz.g2d.getGlowSprite = getGlowSprite;
  window.__viz.g2d.getLabelSprite = getLabelSprite;
})();
