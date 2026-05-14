export interface Command {
  id: string;
  label: string;
  command: string;
  warning?: string;
}

export interface AskResponse {
  answer: string;
  intent: string;
  commands: Command[];
  model_used: boolean;
  source: "llm" | "fixed_reply" | "knowledge_card" | "faq" | "install_template" | "fallback";
  cards_used: string[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  commands?: Command[];
  source?: string;
  timestamp: number;
}

export type ModelStatus = "faq" | "loading" | "available" | "degraded";

export type Language = "zh-CN" | "en" | "zh-TW";

export const QUICK_QUESTIONS_ZH: string[] = [
  "Mnemo 是什么？",
  "我应该怎么安装？",
  "Claude Code 怎么接入？",
  "Cursor 怎么接入？",
  "CodeBuddy 怎么接入？",
  "MCP 注入是什么？",
  "全局提示词注入是什么？",
  "Agent 没有记忆怎么办？",
  "如何验证 Mnemo 是否生效？",
];

export const QUICK_QUESTIONS_EN: string[] = [
  "What is Mnemo?",
  "How to install?",
  "How to connect Claude Code?",
  "How to connect Cursor?",
  "How to connect CodeBuddy?",
  "What is MCP injection?",
  "What is global prompt injection?",
  "Agent has no memory?",
  "How to verify it works?",
];

export const QUICK_QUESTIONS_ZH_TW: string[] = [
  "Mnemo 是什麼？",
  "我應該怎麼安裝？",
  "Claude Code 怎麼接入？",
  "Cursor 怎麼接入？",
  "CodeBuddy 怎麼接入？",
  "MCP 注入是什麼？",
  "全域提示詞注入是什麼？",
  "Agent 沒有記憶怎麼辦？",
  "如何驗證 Mnemo 是否生效？",
];
