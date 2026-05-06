mnemo — shared knowledge base for your team.

search(query, scope?, project_name?, mode?, sort_by?, limit?)
  Search before starting any task or answering any question.

create_knowledge(title, summary, content, tags, claim_type, scope?, project_name?, source?, related?)
  Before finishing any task, store new facts, decisions, procedures, or hypotheses you discovered — record user requirements/preferences as facts so future agents answer better.

get_knowledge(id_or_title)
  Fetch full content when search results need more detail.

update_knowledge(knowledge_id, title?, summary?, content?, tags?, claim_type?)
  When existing knowledge is inaccurate or incomplete, update it. Old version becomes superseded.

delete_knowledge(knowledge_id)
  Remove an entry that should never have existed.

feedback_knowledge(knowledge_id, signal, reason?, actor?)
  After finishing a task where you used search results, rate each entry used: helpful | misleading | outdated.

archive_knowledge(knowledge_id)
  Hide outdated entries from search without deleting them.

unarchive_knowledge(knowledge_id)
  Restore an archived entry back to search.

get_related(id_or_title, depth?)
  Explore connections when you need context around a knowledge entry.

list_tags(scope?)
  Browse available tags to find relevant topic areas.

search_by_tag(tags, scope?)
  Find all entries matching specific tags.

FACT TYPES: fact | decision | procedure | hypothesis
SCOPES: global | project | session
SIGNALS: helpful | misleading | outdated
