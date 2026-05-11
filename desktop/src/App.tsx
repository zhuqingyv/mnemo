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

type Language = "zh-CN" | "en" | "zh-TW";

const I18N: Record<Language, Record<string, string>> = {
  "zh-CN": {
    notInstalled: "未安装",
    linked: "已链接",
    installed: "已安装",
    linkSuccess: "链接成功",
    linkFailed: "链接失败",
    unlinked: "已断开",
    unlinkFailed: "断开失败",
    linkAllSuccess: "全部链接成功",
    linking: "链接中...",
    openInstall: "前往安装",
    unlink: "断开",
    link: "链接",
    linkAll: "全部链接",
    openViz: "打开可视化页面",
    promptHint: "全局提示词未注入",
  },
  en: {
    notInstalled: "Not installed",
    linked: "Linked",
    installed: "Installed",
    linkSuccess: "Linked successfully",
    linkFailed: "Link failed",
    unlinked: "Unlinked",
    unlinkFailed: "Unlink failed",
    linkAllSuccess: "All agents linked",
    linking: "Linking...",
    openInstall: "Install",
    unlink: "Unlink",
    link: "Link",
    linkAll: "Link all",
    openViz: "Open visualization",
    promptHint: "Global prompt not injected",
  },
  "zh-TW": {
    notInstalled: "未安裝",
    linked: "已連結",
    installed: "已安裝",
    linkSuccess: "連結成功",
    linkFailed: "連結失敗",
    unlinked: "已斷開",
    unlinkFailed: "斷開失敗",
    linkAllSuccess: "全部連結成功",
    linking: "連結中...",
    openInstall: "前往安裝",
    unlink: "斷開",
    link: "連結",
    linkAll: "全部連結",
    openViz: "開啟視覺化頁面",
    promptHint: "全域提示詞未注入",
  },
};

function normalizeLanguage(language: string | null | undefined): Language {
  if (!language) return "zh-CN";
  if (language === "zh-CN" || language === "en" || language === "zh-TW") return language;
  const lower = language.toLowerCase();
  if (lower.startsWith("zh-tw") || lower.startsWith("zh-hk") || lower.startsWith("zh-hant")) return "zh-TW";
  if (lower.startsWith("en")) return "en";
  if (lower.startsWith("zh")) return "zh-CN";
  return "zh-CN";
}

interface AgentStatus {
  name: string;
  display_name: string;
  installed: boolean;
  linked: boolean;
  prompt_supported: boolean;
  prompt_injected: boolean;
  install_url: string;
}

function getStatusText(agent: AgentStatus, copy: Record<string, string>): string {
  if (!agent.installed) {
    return copy.notInstalled;
  }
  if (agent.linked) {
    return copy.linked;
  }
  return copy.installed;
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
  const [language, setLanguage] = useState<Language>(() =>
    normalizeLanguage(localStorage.getItem("mnemo_lang") || navigator.language),
  );
  const copy = I18N[language];

  function handleLanguageChange(nextLanguage: Language): void {
    setLanguage(nextLanguage);
    localStorage.setItem("mnemo_lang", nextLanguage);
  }

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
      setMessage(copy.linkSuccess);
    } catch (e) {
      setMessage(`${copy.linkFailed}: ${toMessage(e)}`);
    }
    await refresh();
    setLoading(null);
  }

  async function handleUnlink(name: string) {
    setLoading(name);
    setMessage(null);
    try {
      await invoke("unlink_agent", { name });
      setMessage(copy.unlinked);
    } catch (e) {
      setMessage(`${copy.unlinkFailed}: ${toMessage(e)}`);
    }
    await refresh();
    setLoading(null);
  }

  async function handleLinkAll() {
    setLoading("all");
    setMessage(null);
    try {
      await invoke("link_all");
      setMessage(copy.linkAllSuccess);
    } catch (e) {
      setMessage(`${copy.linkFailed}: ${toMessage(e)}`);
    }
    await refresh();
    setLoading(null);
  }

  async function handleOpenViz() {
    setLoading("viz");
    setMessage(null);
    try {
      await invoke("ensure_mnemo_server");
      await openUrl("http://127.0.0.1:8787/viz");
    } catch (e) {
      setMessage(`打开可视化页面失败: ${toMessage(e)}`);
    }
    setLoading(null);
  }

  return (
    <main className="container">
      <select
        className="language-select"
        value={language}
        aria-label="Language"
        onChange={(event) => handleLanguageChange(event.target.value as Language)}
      >
        <option value="zh-CN">简中</option>
        <option value="en">English</option>
        <option value="zh-TW">繁中</option>
      </select>

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
                <div className="prompt-hint" aria-label={copy.promptHint}>
                  i
                  <span className="prompt-hint-popover">{copy.promptHint}</span>
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
                {getStatusText(agent, copy)}
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
                    ? copy.openInstall
                    : agent.linked
                      ? copy.unlink
                      : copy.link}
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
            {loading === "all" ? copy.linking : copy.linkAll}
          </button>
          <button className="btn-secondary" disabled={loading !== null} onClick={handleOpenViz}>
            {loading === "viz" ? "..." : copy.openViz}
          </button>
        </div>
      </div>

      {message && <p className="message">{message}</p>}
    </main>
  );
}

export default App;
