import { ChatMessage } from "../types";
import { CommandBlock } from "./CommandBlock";

interface AnswerViewProps {
  message: ChatMessage;
}

function renderContent(content: string): string {
  return content
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\n/g, "<br/>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

const SOURCE_LABEL: Record<string, Record<string, string>> = {
  fixed_reply: {
    "zh-CN": "固定回复",
    en: "Fixed reply",
    "zh-TW": "固定回覆",
  },
  knowledge_card: {
    "zh-CN": "知识卡片",
    en: "Knowledge card",
    "zh-TW": "知識卡片",
  },
  faq: { "zh-CN": "FAQ", en: "FAQ", "zh-TW": "FAQ" },
  install_template: {
    "zh-CN": "安装模板",
    en: "Install template",
    "zh-TW": "安裝模板",
  },
  fallback: {
    "zh-CN": "兜底回复",
    en: "Fallback reply",
    "zh-TW": "兜底回覆",
  },
};

export function AnswerView({ message }: AnswerViewProps) {
  if (message.role === "user") {
    return (
      <div className="guide-message guide-message-user">
        <div className="guide-message-content">{message.content}</div>
      </div>
    );
  }

  const sourceLabel = message.source
    ? SOURCE_LABEL[message.source]?.["zh-CN"] ?? message.source
    : null;

  return (
    <div className="guide-message guide-message-assistant">
      <div
        className="guide-message-content"
        dangerouslySetInnerHTML={{ __html: renderContent(message.content) }}
      />
      {message.commands && message.commands.length > 0 && (
        <div className="guide-commands-area">
          {message.commands.map((cmd) => (
            <CommandBlock key={cmd.id} command={cmd} />
          ))}
        </div>
      )}
      {sourceLabel && (
        <div className="guide-message-source">{sourceLabel}</div>
      )}
    </div>
  );
}
