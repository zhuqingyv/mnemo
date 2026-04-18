(function(){
  window.__viz = window.__viz || {};
  window.__viz.i18n = window.__viz.i18n || {};
  window.__viz.i18n['zh-CN'] = {
    // nav
    'nav.home': '首页',
    'nav.graph': '图谱',
    'nav.notes': '笔记',
    'nav.settings': '设置',
    // topbar
    'topbar.search_placeholder': '搜索知识库…(走真实 /api/v1/knowledge/search)',
    'topbar.refresh': '刷新',
    'topbar.last_update': '最后更新',
    'topbar.just_now': '刚刚',
    'topbar.online': '在线',
    'topbar.offline': '离线',
    // dashboard
    'dash.overview': '概览',
    'dash.knowledge_total': '总知识数',
    'dash.search_calls': '搜索调用',
    'dash.feedback_quality': '反馈质量',
    'dash.contradictions': '矛盾对',
    'dash.active': '活跃',
    'dash.stale': '停用',
    'dash.superseded': '被覆盖',
    'dash.archived': '已归档',
    'dash.helpful': '有用',
    'dash.misleading': '误导',
    'dash.outdated': '过时',
    // tool chart
    'tools.title': '工具调用统计',
    'tools.subtitle': '监控事件 · 全部时间',
    'tools.call_counts': '工具调用次数',
    // tool names
    'tool.search': '搜索',
    'tool.feedback_knowledge': '反馈知识',
    'tool.create_knowledge': '创建知识',
    'tool.get_knowledge': '获取知识',
    'tool.update_knowledge': '更新知识',
    'tool.archive_knowledge': '归档知识',
    'tool.delete_knowledge': '删除知识',
    'tool.list_tags': '列出标签',
    'tool.search_by_tag': '按标签搜索',
    // knowledge list
    'list.title': '知识库',
    'list.no_results': '没有结果',
    'list.loading': '加载中…',
    'list.search_hint': '搜索结果',
    // detail
    'detail.summary': '摘要',
    'detail.content': '内容',
    'detail.feedback': '反馈',
    'detail.relations': '关联',
    'detail.no_feedback': '暂无反馈',
    'detail.no_relations': '无关联',
    'detail.superseded_by': '已被取代 →',
    'detail.last_hit': '最后命中',
    // graph
    'graph.nodes': '节点',
    'graph.edges': '边',
    'graph.legend': '图例',
    'graph.metrics': '实时指标',
    // time
    'time.seconds_ago': '秒前',
    'time.minutes_ago': '分钟前',
    'time.hours_ago': '小时前',
    'time.days_ago': '天前',
    // status
    'status.relation_contradicts': '关系对 · 类型=contradicts',
    'status.tool_search': '工具: search · 事件: monitor_event 全量',
  };
})();
