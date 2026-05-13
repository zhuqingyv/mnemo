import { useState } from "react";
import { Command } from "../types";

interface CommandBlockProps {
  command: Command;
}

export function CommandBlock({ command }: CommandBlockProps) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(command.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = command.command;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  return (
    <div className="guide-command-block">
      <div className="guide-command-header">
        <span className="guide-command-label">{command.label}</span>
        <button className="guide-copy-btn" onClick={handleCopy} type="button">
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre className="guide-command-code">
        <code>{command.command}</code>
      </pre>
      {command.warning && (
        <p className="guide-command-warning">{command.warning}</p>
      )}
    </div>
  );
}
