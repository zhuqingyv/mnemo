use std::process::Command;
use std::sync::OnceLock;
use serde::{Deserialize, Serialize};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

static RESOURCE_DIR: OnceLock<Option<std::path::PathBuf>> = OnceLock::new();

#[derive(Serialize, Deserialize, Clone)]
struct AgentStatus {
    name: String,
    display_name: String,
    installed: bool,
    detected: bool,
}

#[tauri::command]
fn detect_agents() -> Vec<AgentStatus> {
    // (id, display_name, config_path, binary_names_to_check)
    let agents: Vec<(&str, &str, &str, Vec<&str>)> = vec![
        ("claude-code", "Claude Code", "~/.claude.json", vec!["claude"]),
        ("qwen-code", "Qwen Code", "~/.qwen/settings.json", vec!["qwen"]),
        ("codebuddy", "CodeBuddy", "~/.codebuddy/.mcp.json", vec!["cbc", "codebuddy"]),
        ("codex-cli", "Codex CLI", "~/.codex/config.toml", vec!["codex"]),
        ("gemini-cli", "Gemini CLI", "~/.gemini/settings.json", vec!["gemini"]),
        ("cursor", "Cursor", "~/.cursor/mcp.json", vec!["cursor"]),
        ("windsurf", "Windsurf", "~/.codeium/windsurf/mcp_config.json", vec![]),
        ("github-copilot-cli", "GitHub Copilot CLI", "~/.copilot/mcp-config.json", vec!["copilot"]),
    ];

    let home = dirs_home();
    agents
        .into_iter()
        .map(|(name, display, config_path, binaries)| {
            let full_path = config_path.replace("~", &home);
            let config_exists = std::path::Path::new(&full_path).exists();
            let binary_found = binaries.iter().any(|bin| which::which(bin).is_ok());
            let detected = config_exists || binary_found;
            let installed = if config_exists {
                check_mnemo_installed(&full_path, name)
            } else {
                false
            };
            AgentStatus {
                name: name.to_string(),
                display_name: display.to_string(),
                installed,
                detected,
            }
        })
        .collect()
}

#[tauri::command]
async fn install_agent(name: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_mnemo_setup(&["setup"], Some(&name)))
        .await
        .map_err(|e| format!("Task join error: {}", e))?
}

#[tauri::command]
async fn uninstall_agent(name: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_mnemo_setup(&["setup", "--uninstall"], Some(&name)))
        .await
        .map_err(|e| format!("Task join error: {}", e))?
}

#[tauri::command]
async fn install_all() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_mnemo_setup(&["setup"], None))
        .await
        .map_err(|e| format!("Task join error: {}", e))?
}

#[tauri::command]
async fn uninstall_all() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_mnemo_setup(&["setup", "--uninstall"], None))
        .await
        .map_err(|e| format!("Task join error: {}", e))?
}

fn run_mnemo_setup(args: &[&str], agent: Option<&str>) -> Result<String, String> {
    let mnemo = find_mnemo_binary();
    let mut cmd = Command::new(&mnemo);
    cmd.args(args);
    // Always use HTTP mode — mnemo server is always running via the desktop app.
    // This ensures instant connect (no cold-start spawn of heavy Python binary).
    if !args.contains(&"--uninstall") {
        cmd.args(["--mode", "http"]);
    }
    if let Some(name) = agent {
        cmd.args(["--client", name]);
    }
    let output = cmd
        .output()
        .map_err(|e| format!("Failed to run mnemo: {}", e))?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).to_string())
    }
}

fn find_mnemo_binary() -> String {
    // 1. Bundled resource: Tauri 2.x may place resources directly in the
    //    resource_dir, or nest them under resource_dir/Resources/.  We try
    //    both locations, with the platform-appropriate binary name.
    let bin_name = if cfg!(windows) { "mnemo.exe" } else { "mnemo" };

    if let Some(Some(res_dir)) = RESOURCE_DIR.get() {
        let candidates = [
            res_dir.join(bin_name),                       // flat (Tauri 2 resource flattening)
            res_dir.join("Resources").join(bin_name),     // macOS .app bundle layout
            res_dir.join("resources").join(bin_name),     // dev-time path
        ];
        for path in &candidates {
            if path.exists() {
                return path.to_string_lossy().to_string();
            }
        }
    }

    // 2. Common install paths on the host filesystem
    let home = dirs_home();
    let fs_candidates = [
        format!("{home}/.local/bin/{bin_name}"),
        format!("{home}/.mnemo/bin/{bin_name}"),
        "/usr/local/bin/".to_string() + bin_name,
    ];
    for path in &fs_candidates {
        if std::path::Path::new(path).exists() {
            return path.clone();
        }
    }

    // 3. PATH lookup
    if let Ok(path) = which::which(bin_name) {
        return path.to_string_lossy().to_string();
    }

    // 4. Last resort — let the shell fail gracefully
    bin_name.to_string()
}

fn dirs_home() -> String {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| "~".to_string())
}

fn check_mnemo_installed(config_path: &str, name: &str) -> bool {
    let content = match std::fs::read_to_string(config_path) {
        Ok(c) => c,
        Err(_) => return false,
    };

    if name == "codex-cli" {
        return content.contains("[mcp_servers.mnemo]");
    }

    let data: serde_json::Value = match serde_json::from_str(&content) {
        Ok(v) => v,
        Err(_) => return false,
    };

    // Check top-level mcpServers.mnemo
    if data.get("mcpServers").and_then(|s| s.get("mnemo")).is_some() {
        return true;
    }

    // Check project-level: projects.*.mcpServers.mnemo (Claude Code stores per-project configs)
    if let Some(projects) = data.get("projects").and_then(|p| p.as_object()) {
        for (_proj_path, proj_val) in projects {
            if proj_val.get("mcpServers").and_then(|s| s.get("mnemo")).is_some() {
                return true;
            }
        }
    }

    false
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Store resource path for finding bundled mnemo binary
            let res_path = app.path().resource_dir().ok();
            let _ = RESOURCE_DIR.set(res_path);

            // Build tray menu
            let show = MenuItem::with_id(app, "show", "打开面板", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出 mnemo", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;

            // Create tray icon
            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .tooltip("mnemo")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            // Hide from dock on macOS
            #[cfg(target_os = "macos")]
            {
                app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            // Close window = hide, don't quit
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .invoke_handler(tauri::generate_handler![
            detect_agents,
            install_agent,
            uninstall_agent,
            install_all,
            uninstall_all,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
