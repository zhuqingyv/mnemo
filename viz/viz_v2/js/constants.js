/**
 * js/constants.js
 *
 * 职责：所有大写常量的统一出处（physics/animation/render/3D）
 * 依赖：无
 * 注册：__viz.const = { TYPE_RANK, MAX_GRAPH_NODES, PULSE_*, ALPHA_*, FADE_*, COLOR_TWEEN_MS, HALO_DURATION_MS, PROJECT_HUES_3D, EDGE_STYLES_3D }
 */
(function () {
  'use strict';

  // ===== node/edge construction (源 1582, 1627) =====
  const TYPE_RANK = { contradicts: 3, supersedes: 2, related: 1 };
  const MAX_GRAPH_NODES = 2000;

  // ===== live search-pulse polling (源 1878–1881) =====
  const PULSE_WINDOW_SECONDS = 5;
  const PULSE_DURATION_MS = 800;
  const PULSE_MAX_RADIUS = 26;
  const PULSE_SEEN_CAP = 500;

  // ===== physics alpha (源 1971–1973) =====
  const ALPHA_DECAY = 0.98;
  const ALPHA_MIN = 0.001;
  const ALPHA_RESTART = 1.0;

  // ===== fade / color tween / halo (源 2100–2104) =====
  const FADE_IN_MS = 300;
  const FADE_OUT_MS = 300;
  const EDGE_FADE_IN_MS = 500;
  const COLOR_TWEEN_MS = 300;
  const HALO_DURATION_MS = 2000;

  // ===== 3D color / edge style (源 2849, 2871–2876) =====
  const PROJECT_HUES_3D = [210, 140, 30, 280, 350, 170, 60, 310, 100, 240, 0, 190, 330, 80, 260];
  const EDGE_STYLES_3D = {
    related:      { color: 'rgba(88,166,255,0.35)', width: 0.6 },
    auto_related: { color: 'rgba(210,153,34,0.38)', width: 0.5 },
    supersedes:   { color: '#a371f7',                width: 1.2 },
    contradicts:  { color: '#f85149',                width: 2.2 },
  };

  // ---- 注册到 __viz.const ----
  window.__viz = window.__viz || {};
  window.__viz.const = {
    TYPE_RANK,
    MAX_GRAPH_NODES,
    PULSE_WINDOW_SECONDS,
    PULSE_DURATION_MS,
    PULSE_MAX_RADIUS,
    PULSE_SEEN_CAP,
    ALPHA_DECAY,
    ALPHA_MIN,
    ALPHA_RESTART,
    FADE_IN_MS,
    FADE_OUT_MS,
    EDGE_FADE_IN_MS,
    COLOR_TWEEN_MS,
    HALO_DURATION_MS,
    PROJECT_HUES_3D,
    EDGE_STYLES_3D,
  };
})();
