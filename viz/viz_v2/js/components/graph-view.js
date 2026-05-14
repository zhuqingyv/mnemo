/**
 * js/components/graph-view.js
 *
 * 职责：2D 图谱容器（GraphView）+ 3D 图谱容器（Graph3DView）两个 nova-dom 组件
 * 依赖：__viz.NV, __viz.DomTags, __viz.state, __viz.comp.{GraphLegend, GraphMetricsPanel}
 *       __viz.g2d.interactions.{resizeCanvas, wireGraphInteractions}
 *       __viz.g2d.build.buildGraph
 *       __viz.g2d.loop.{startGraphLoop, stopGraphLoop}
 *       __viz.g2d.updateSearchMatch
 *       __viz.g3d.lifecycle.{activate, deactivate, updateSearchMatch}
 *       __viz.polling.stopPolling
 * 注册：__viz.comp.GraphView, __viz.comp.Graph3DView
 */
(function () {
  'use strict';

  const { component, watch, onMounted, onUnmounted } = window.__viz.NV;
  const { div, canvas } = window.__viz.DomTags;
  const state = window.__viz.state;

  // index.html style 块保留：
  //   #graph-view / #graph-3d-view 的 fixed 定位 + .show 切 display:block
  //   #graph-3d-container width/height 100%
  //   #graph-canvas width/height 100% + cursor
  //   #label-layer 绝对定位 pointer-events:none
  // 这些都用 id 选择器，不需要 Tailwind class。
  // `show` class 必须保留（JS 逻辑未触及，由 index.html 的 #graph-view.show 规则切 display）。

  const TOOLTIP_CLS = 'absolute pointer-events-none z-10 hidden ' +
                      'bg-surface-1 border border-border-strong rounded px-2.5 py-1.5 ' +
                      'text-[11px] text-text-0 max-w-[320px] leading-snug ' +
                      'shadow-[0_4px_14px_rgba(0,0,0,0.5)]';

  const LOADING_CLS = 'absolute inset-0 flex items-center justify-center ' +
                      'bg-surface-0/60 text-text-2 text-[12px]';

  // ============ Graph3DView (container) ============
  // NOTE: nova-dom's Show → component() composition does NOT forward inner
  // onUnmounted hooks or watch teardowns to Show's unmount scope. So any
  // watch() here will KEEP FIRING after the component is visually unmounted.
  // Hard-fix: every callback here must bail when state.view !== '3d'.
  // Final-teardown (deactivate) is driven from app.js's state.view watcher.
  // ============ Graph3DView (pure DOM factory) ============
  function Graph3DView() {
    var el = div()
      .id('graph-3d-view')
      .class('show')(
        div().id('graph-3d-container')(),
        div().id('label-layer')(),
      );
    requestAnimationFrame(function() {
      window.__viz.g3d.lifecycle.activate();
    });
    return el;
  }

  // ============ GraphView (pure DOM factory) ============
  // NOT a component() — nova-dom's Show does not forward onUnmounted to
  // inner components, so lifecycle is managed by app.js watch(state.view).
  function GraphView() {
    var GraphLegend = window.__viz.comp.GraphLegend;
    var GraphMetricsPanel = window.__viz.comp.GraphMetricsPanel;
    var el = div()
      .id('graph-view')
      .class('show')(
        canvas().id('graph-canvas')(),
        GraphLegend(),
        div().id('graph-tooltip').class(TOOLTIP_CLS)(),
        div().id('graph-loading').class(LOADING_CLS + ' hidden')('building graph…'),
        GraphMetricsPanel(),
      );
    // Init after DOM is in tree (Show inserts, then we wire)
    requestAnimationFrame(function() {
      window.__viz.g2d.interactions.resizeCanvas();
      window.__viz.g2d.interactions.wireGraphInteractions();
      if (!state.graph.built && state.knowledge.length) {
        window.__viz.g2d.build.buildGraph();
      } else {
        window.__viz.g2d.loop.startGraphLoop();
      }
    });
    return el;
  }

  // ---- 注册 ----
  window.__viz.comp = window.__viz.comp || {};
  window.__viz.comp.GraphView = GraphView;
  window.__viz.comp.Graph3DView = Graph3DView;
})();
