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

const AGENT_ICONS: Record<string, string> = {
  "claude-code": claudeIcon,
  "qwen-code": qwenIcon,
  codebuddy: codebuddyIcon,
  "codex-cli": codexIcon,
  "gemini-cli": geminiIcon,
  cursor: cursorIcon,
};

interface AgentStatus {
  name: string;
  display_name: string;
  installed: boolean;
  detected: boolean;
}

function App() {
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [loading, setLoading] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function refresh() {
    const result = await invoke<AgentStatus[]>("detect_agents");
    setAgents(result);
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleInstall(name: string) {
    setLoading(name);
    setMessage(null);
    try {
      await invoke("install_agent", { name });
      setMessage("安装成功");
    } catch (e) {
      setMessage(`安装失败: ${e}`);
    }
    await refresh();
    setLoading(null);
  }

  async function handleUninstall(name: string) {
    setLoading(name);
    setMessage(null);
    try {
      await invoke("uninstall_agent", { name });
      setMessage("已卸载");
    } catch (e) {
      setMessage(`卸载失败: ${e}`);
    }
    await refresh();
    setLoading(null);
  }

  async function handleInstallAll() {
    setLoading("all");
    setMessage(null);
    try {
      await invoke("install_all");
      setMessage("全部安装成功");
    } catch (e) {
      setMessage(`安装失败: ${e}`);
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
              className={`agent-card ${!agent.detected ? "disabled" : ""} ${agent.installed ? "installed" : ""}`}
            >
              <img
                className="agent-icon"
                src={AGENT_ICONS[agent.name]}
                alt={agent.display_name}
              />
              <div className="agent-name">{agent.display_name}</div>
              <div className={`agent-status ${agent.installed ? "status-installed" : ""}`}>
                <span className="status-dot" />
                {!agent.detected
                  ? "未检测到"
                  : agent.installed
                    ? "已安装"
                    : "未安装"}
              </div>
              {agent.detected && (
                <button
                  className={agent.installed ? "btn-uninstall" : "btn-install"}
                  disabled={loading !== null}
                  onClick={() =>
                    agent.installed
                      ? handleUninstall(agent.name)
                      : handleInstall(agent.name)
                  }
                >
                  {loading === agent.name
                    ? "..."
                    : agent.installed
                      ? "卸载"
                      : "安装"}
                </button>
              )}
            </div>
          ))}
        </div>

        <div className="actions">
          <button
            className="btn-primary"
            disabled={loading !== null}
            onClick={handleInstallAll}
          >
            {loading === "all" ? "安装中..." : "全部安装"}
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
