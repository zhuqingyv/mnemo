import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import "./App.css";

import mnemoLogo from "./assets/mnemo-logo.png";
import claudeIcon from "./assets/agents/claude.svg";
import qwenIcon from "./assets/agents/qwen.svg";
import codebuddyIcon from "./assets/agents/codebuddy.svg";
import codexIcon from "./assets/agents/codex.svg";
import geminiIcon from "./assets/agents/gemini.svg";
import cursorIcon from "./assets/agents/cursor.svg";
import windsurfIcon from "./assets/agents/windsurf.png";
import copilotIcon from "./assets/agents/copilot.svg";

const AGENT_ICONS: Record<string, string> = {
  "claude-code": claudeIcon,
  "qwen-code": qwenIcon,
  codebuddy: codebuddyIcon,
  "codex-cli": codexIcon,
  "gemini-cli": geminiIcon,
  cursor: cursorIcon,
  windsurf: windsurfIcon,
  "github-copilot-cli": copilotIcon,
};

interface AgentStatus {
  name: string;
  display_name: string;
  installed: boolean;
  linked: boolean;
  prompt_supported: boolean;
  prompt_injected: boolean;
  install_url: string;
}

function getStatusText(agent: AgentStatus): string {
  if (!agent.installed) {
    return "未安装";
  }
  if (agent.linked) {
    return "已链接";
  }
  return "已安装";
}

function getInitials(name: string): string {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function toMessage(error: unknown): string {
  return String(error).replace(/\s+/g, " ").trim();
}

function shouldShowPromptHint(agent: AgentStatus): boolean {
  return agent.installed && agent.linked && agent.prompt_supported && !agent.prompt_injected;
}

function App() {
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [loading, setLoading] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [brokenIcons, setBrokenIcons] = useState<Set<string>>(new Set());

  async function refresh() {
    const result = await invoke<AgentStatus[]>("detect_agents");
    setAgents(result);
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleOpenInstall(agent: AgentStatus) {
    if (agent.install_url) {
      await openUrl(agent.install_url);
    }
  }

  async function handleLink(name: string) {
    setLoading(name);
    setMessage(null);
    try {
      await invoke("link_agent", { name });
      setMessage("链接成功");
    } catch (e) {
      setMessage(`链接失败: ${toMessage(e)}`);
    }
    await refresh();
    setLoading(null);
  }

  async function handleUnlink(name: string) {
    setLoading(name);
    setMessage(null);
    try {
      await invoke("unlink_agent", { name });
      setMessage("已断开");
    } catch (e) {
      setMessage(`断开失败: ${toMessage(e)}`);
    }
    await refresh();
    setLoading(null);
  }

  async function handleLinkAll() {
    setLoading("all");
    setMessage(null);
    try {
      await invoke("link_all");
      setMessage("全部链接成功");
    } catch (e) {
      setMessage(`链接失败: ${toMessage(e)}`);
    }
    await refresh();
    setLoading(null);
  }

  async function handleOpenViz() {
    await openUrl("http://127.0.0.1:8787/viz");
  }

  return (
    <main className="container">

      <header className="header">
        <img src={mnemoLogo} alt="mnemo" className="mnemo-logo" />
      </header>

      <div className={`card-area ${loading !== null ? "is-loading" : ""}`}>
        {loading !== null && (
          <div className="loading-overlay">
            <div className="spinner" />
          </div>
        )}
        <div className="agents-grid">
          {agents.map((agent) => (
            <div
              key={agent.name}
              className={`agent-card ${agent.installed && agent.linked ? "linked" : ""}`}
            >
              {shouldShowPromptHint(agent) && (
                <div className="prompt-hint" aria-label="全局提示词未注入">
                  i
                  <span className="prompt-hint-popover">全局提示词未注入</span>
                </div>
              )}
              {brokenIcons.has(agent.name) || !AGENT_ICONS[agent.name] ? (
                <div className="agent-icon-fallback">
                  {getInitials(agent.display_name)}
                </div>
              ) : (
                <img
                  className="agent-icon"
                  src={AGENT_ICONS[agent.name]}
                  alt={agent.display_name}
                  onError={() =>
                    setBrokenIcons((prev) => new Set(prev).add(agent.name))
                  }
                />
              )}
              <div className="agent-name">{agent.display_name}</div>
              <div className={`agent-status ${agent.installed && agent.linked ? "status-linked" : ""}`}>
                <span className="status-dot" />
                {getStatusText(agent)}
              </div>
              <button
                className={agent.installed ? "btn-link" : "btn-install"}
                disabled={loading !== null}
                onClick={() => {
                  if (!agent.installed) {
                    handleOpenInstall(agent);
                    return;
                  }
                  if (agent.linked) {
                    handleUnlink(agent.name);
                    return;
                  }
                  handleLink(agent.name);
                }}
              >
                {loading === agent.name
                  ? "..."
                  : !agent.installed
                    ? "前往安装"
                    : agent.linked
                      ? "断开"
                      : "链接"}
              </button>
            </div>
          ))}
        </div>

        <div className="actions">
          <button
            className="btn-primary"
            disabled={loading !== null}
            onClick={handleLinkAll}
          >
            {loading === "all" ? "链接中..." : "全部链接"}
          </button>
          <button className="btn-secondary" onClick={handleOpenViz}>
            打开可视化页面
          </button>
        </div>
      </div>

      {message && <p className="message">{message}</p>}
    </main>
  );
}

export default App;
