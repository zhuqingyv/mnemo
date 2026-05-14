use serde::{Deserialize, Serialize};
use std::net::TcpStream;
#[cfg(unix)]
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

mod interface;
use interface::router::Router;
use interface::server::WsGateway;

static RESOURCE_DIR: OnceLock<Option<std::path::PathBuf>> = OnceLock::new();
static MNEMO_SERVER: OnceLock<Mutex<Option<Child>>> = OnceLock::new();
static WS_GATEWAY: OnceLock<Arc<WsGateway>> = OnceLock::new();

#[derive(Serialize, Deserialize, Clone)]
struct AgentStatus {
    name: String,
    display_name: String,
    installed: bool,
    linked: bool,
    prompt_supported: bool,
    prompt_injected: bool,
    install_url: String,
}

/// Initialize the WS Interface Gateway: builds the router, registers
/// system handlers, and starts listening on port 8788.
fn init_interface_gateway() {
    let mut router = Router::new();

    // --- agent.* handlers ---
    router.register_system("agent.detect", |_params| {
        let list = detect_agents();
        let json = serde_json::to_value(list).map_err(|e| {
            interface::protocol::RpcErrorDetail {
                code: -32603,
                message: format!("Serialization error: {}", e),
                data: None,
            }
        })?;
        Ok(serde_json::json!({ "agents": json }))
    });

    router.register_system("agent.link", |params| {
        let name = params["name"]
            .as_str()
            .unwrap_or("")
            .to_string();
        // Ensure server is running
        ensure_mnemo_server_running()
            .map_err(|e| interface::protocol::RpcErrorDetail {
                code: -32000,
                message: e,
                data: None,
            })?;
        let name_str = name.as_str();
        match run_mnemo_setup(&["setup", "--no-project-prompts"], Some(name_str)) {
            Ok(output) => Ok(serde_json::json!({ "status": "ok", "output": output })),
            Err(e) => Err(interface::protocol::RpcErrorDetail {
                code: -32000,
                message: e,
                data: None,
            }),
        }
    });

    router.register_system("agent.unlink", |params| {
        let name = params["name"]
            .as_str()
            .unwrap_or("")
            .to_string();
        let name_str = name.as_str();
        match run_mnemo_setup(&["setup", "--uninstall", "--mcp-only"], Some(name_str)) {
            Ok(output) => Ok(serde_json::json!({ "status": "ok", "output": output })),
            Err(e) => Err(interface::protocol::RpcErrorDetail {
                code: -32000,
                message: e,
                data: None,
            }),
        }
    });

    router.register_system("agent.link_all", |_params| {
        ensure_mnemo_server_running()
            .map_err(|e| interface::protocol::RpcErrorDetail {
                code: -32000,
                message: e,
                data: None,
            })?;
        match run_mnemo_setup(&["setup", "--no-project-prompts"], None) {
            Ok(output) => Ok(serde_json::json!({ "status": "ok", "output": output })),
            Err(e) => Err(interface::protocol::RpcErrorDetail {
                code: -32000,
                message: e,
                data: None,
            }),
        }
    });

    router.register_system("agent.unlink_all", |_params| {
        match run_mnemo_setup(&["setup", "--uninstall", "--mcp-only"], None) {
            Ok(output) => Ok(serde_json::json!({ "status": "ok", "output": output })),
            Err(e) => Err(interface::protocol::RpcErrorDetail {
                code: -32000,
                message: e,
                data: None,
            }),
        }
    });

    // --- system.* handlers ---
    router.register_system("system.ensure_server", |_params| {
        match ensure_mnemo_server_running() {
            Ok(msg) => Ok(serde_json::json!({ "status": msg })),
            Err(e) => Ok(serde_json::json!({ "status": "error", "message": e })),
        }
    });

    router.register_system("system.status", |_params| {
        let server_running = server_responds();
        Ok(serde_json::json!({
            "server_running": server_running,
        }))
    });

    // --- cli.* handlers ---
    router.register_system("cli.sync", |_params| {
        // CLI version sync: compare bundled binary vs system binary,
        // copy if bundled is newer.
        let updated = sync_cli_binary();
        Ok(serde_json::json!({ "synced": updated }))
    });

    // Register backend-forwarded methods
    for method in &[
        "guide.ask",
        "knowledge.create",
        "knowledge.search",
        "knowledge.get",
        "knowledge.update",
        "knowledge.delete",
        "knowledge.feedback",
        "knowledge.related",
        "knowledge.tags",
        "knowledge.by_tag",
        "stats.overview",
        "stats.timeline",
        "stats.events",
    ] {
        router.register_backend(method);
    }

    let gateway = Arc::new(WsGateway::new(router, 8787));
    let gateway_clone = gateway.clone();

    // Store for push_event access
    let _ = WS_GATEWAY.set(gateway);

    // Spawn the WS server on port 8788
    tauri::async_runtime::spawn(async move {
        if let Err(e) = gateway_clone.start(8788).await {
            eprintln!("WS Gateway error: {}", e);
        }
    });
}

/// Sync bundled CLI binary to system install path if bundled is newer.
/// Returns true if an update was performed.
fn sync_cli_binary() -> bool {
    let _bundled = find_mnemo_binary();
    // Only proceed if we found the bundled binary (resource directory)
    let res_dir = RESOURCE_DIR.get().and_then(|o| o.as_ref());
    let bundled_path = res_dir.and_then(|d| {
        let bin = if cfg!(windows) { "mnemo.exe" } else { "mnemo" };
        let candidates = [
            d.join(bin),
            d.join("Resources").join(bin),
            d.join("resources").join(bin),
        ];
        candidates.into_iter().find(|p| p.exists())
    });

    if bundled_path.is_none() {
        return false;
    }
    let bundled_path = bundled_path.unwrap();

    let home = dirs_home();
    let system_path =
        std::path::PathBuf::from(format!("{}/.mnemo/bin/{}", home,
            if cfg!(windows) { "mnemo.exe" } else { "mnemo" }));

    if !system_path.exists() {
        // No system binary yet — install it
        if let Some(parent) = system_path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        return std::fs::copy(&bundled_path, &system_path).is_ok();
    }

    // Compare versions
    let bundled_version = get_binary_version(&bundled_path);
    let system_version = get_binary_version(&system_path);

    match (bundled_version, system_version) {
        (Some(bv), Some(sv)) if bv > sv => {
            let _ = std::fs::copy(&bundled_path, &system_path);
            true
        }
        (Some(_), None) => {
            let _ = std::fs::copy(&bundled_path, &system_path);
            true
        }
        _ => false,
    }
}

/// Get version string from a mnemo binary
fn get_binary_version(path: &std::path::Path) -> Option<String> {
    Command::new(path)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .ok()
        .and_then(|output| {
            if output.status.success() {
                String::from_utf8(output.stdout)
                    .ok()
                    .map(|s| s.trim().to_string())
            } else {
                None
            }
        })
}

fn detect_agents() -> Vec<AgentStatus> {
    let agents: Vec<(&str, &str, &str, Vec<&str>, Vec<&str>, Vec<&str>, &str)> = vec![
        ("claude-code", "Claude Code", "~/.claude.json", vec!["claude"], vec![], vec!["~/.claude/CLAUDE.md"], "https://docs.anthropic.com/en/docs/claude-code/setup"),
        ("qwen-code", "Qwen Code", "~/.qwen/settings.json", vec!["qwen"], vec![], vec!["~/.qwen/QWEN.md"], "https://github.com/QwenLM/qwen-code"),
        ("codebuddy", "CodeBuddy", "~/.codebuddy/.mcp.json", vec!["cbc", "codebuddy"], vec![], vec!["~/.codebuddy/CODEBUDDY.md"], "https://www.codebuddy.ai/"),
        ("codex-cli", "Codex CLI", "~/.codex/config.toml", vec!["codex"], vec![], vec![], "https://github.com/openai/codex"),
        ("gemini-cli", "Gemini CLI", "~/.gemini/settings.json", vec!["gemini"], vec![], vec!["~/.gemini/GEMINI.md"], "https://github.com/google-gemini/gemini-cli"),
        ("cursor", "Cursor", "~/.cursor/mcp.json", vec!["cursor"], vec!["/Applications/Cursor.app"], vec![], "https://cursor.com/downloads"),
        ("windsurf", "Windsurf", "~/.codeium/windsurf/mcp_config.json", vec!["windsurf"], vec!["/Applications/Windsurf.app"], vec!["~/.codeium/windsurf/memories/global_rules.md"], "https://windsurf.com/download"),
        ("github-copilot-cli", "GitHub Copilot CLI", "~/.copilot/mcp-config.json", vec!["copilot"], vec![], vec!["~/.copilot/copilot-instructions.md"], "https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/use-copilot-cli"),
    ];

    let home = dirs_home();
    agents
        .into_iter()
        .map(
            |(name, display, config_path, binaries, app_paths, prompt_paths, install_url)| {
                let full_path = config_path.replace("~", &home);
                let config_exists = std::path::Path::new(&full_path).exists();
                let installed = agent_installed(name, &binaries, &app_paths);
                let linked = if config_exists {
                    check_mnemo_installed(&full_path, name)
                } else {
                    false
                };
                let prompt_supported = !prompt_paths.is_empty();
                let prompt_injected = prompt_supported
                    && prompt_paths
                        .iter()
                        .any(|path| check_prompt_injected(&path.replace("~", &home)));
                AgentStatus {
                    name: name.to_string(),
                    display_name: display.to_string(),
                    installed,
                    linked,
                    prompt_supported,
                    prompt_injected,
                    install_url: install_url.to_string(),
                }
            },
        )
        .collect()
}

fn ensure_mnemo_server_running() -> Result<String, String> {
    if server_responds() {
        return Ok("mnemo server already running".to_string());
    }

    let server = MNEMO_SERVER.get_or_init(|| Mutex::new(None));
    let mut guard = server
        .lock()
        .map_err(|_| "Failed to lock mnemo server state".to_string())?;

    if let Some(child) = guard.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            drop(guard);
            wait_for_server()?;
            return Ok("mnemo server started".to_string());
        }
    }

    let mnemo = find_mnemo_binary();
    let mut cmd = Command::new(&mnemo);
    cmd.args(["serve", "--host", "127.0.0.1", "--port", "8787"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // Place in its own process group — PyInstaller onefile forks on
    // macOS/Linux, so killing only the direct child leaves the orphan alive.
    #[cfg(unix)]
    unsafe { cmd.pre_exec(|| { libc::setpgid(0, 0); Ok(()) }); }
    let child = cmd.spawn()
        .map_err(|e| format!("Failed to start mnemo server with {mnemo}: {e}"))?;

    *guard = Some(child);
    drop(guard);
    wait_for_server()?;
    Ok("mnemo server started".to_string())
}

fn server_responds() -> bool {
    TcpStream::connect_timeout(
        &"127.0.0.1:8787".parse().expect("valid socket address"),
        Duration::from_millis(150),
    )
    .is_ok()
}

fn wait_for_server() -> Result<(), String> {
    for _ in 0..40 {
        if server_responds() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Err("mnemo server did not start on 127.0.0.1:8787".to_string())
}

fn stop_mnemo_server() {
    if let Some(server) = MNEMO_SERVER.get() {
        if let Ok(mut guard) = server.lock() {
            if let Some(child) = guard.as_mut() {
                #[cfg(unix)]
                {
                    // Kill entire process group — PyInstaller onefile forks on
                    // macOS/Linux, leaving the server orphaned when only the
                    // direct child is killed.
                    let pid = child.id();
                    unsafe { libc::kill(-(pid as libc::pid_t), libc::SIGKILL); }
                }
                let _ = child.kill();
                let _ = child.wait();
            }
            *guard = None;
        }
    }
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
            res_dir.join(bin_name),                   // flat (Tauri 2 resource flattening)
            res_dir.join("Resources").join(bin_name), // macOS .app bundle layout
            res_dir.join("resources").join(bin_name), // dev-time path
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

fn command_succeeds(program: &str, args: &[&str]) -> bool {
    if Command::new(program)
        .args(args)
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
    {
        return true;
    }

    if let Some(path) = resolve_binary(program) {
        if Command::new(path)
            .args(args)
            .output()
            .map(|output| output.status.success())
            .unwrap_or(false)
        {
            return true;
        }
    }

    shell_command_succeeds(program, args)
}

fn shell_token_safe(value: &str) -> bool {
    !value.is_empty()
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
}

fn shell_command_succeeds(program: &str, args: &[&str]) -> bool {
    if !shell_token_safe(program) || !args.iter().all(|arg| shell_token_safe(arg)) {
        return false;
    }

    let command = if args.is_empty() {
        format!("{program} >/dev/null 2>&1")
    } else {
        format!("{program} {} >/dev/null 2>&1", args.join(" "))
    };

    // Use interactive login shell (-lic) so that .zshrc PATH setup
    // (nvm, fnm, etc.) takes effect even when spawned from a GUI app.
    let mut child = match Command::new("/bin/zsh")
        .args(["-lic", &command])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(child) => child,
        Err(_) => return false,
    };

    // Wait up to 5 seconds to prevent hangs from slow shell init.
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) => {
                if std::time::Instant::now() > deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return false;
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Err(_) => return false,
        }
    }
}

fn resolve_binary(binary: &str) -> Option<std::path::PathBuf> {
    if let Ok(path) = which::which(binary) {
        return Some(path);
    }

    binary_candidate_paths(binary)
        .into_iter()
        .find(|path| path.exists())
}

fn binary_candidate_paths(binary: &str) -> Vec<std::path::PathBuf> {
    let home = dirs_home();
    let mut paths = vec![
        std::path::PathBuf::from(format!("{home}/.local/bin/{binary}")),
        std::path::PathBuf::from(format!("{home}/.mnemo/bin/{binary}")),
        std::path::PathBuf::from(format!("/opt/homebrew/bin/{binary}")),
        std::path::PathBuf::from(format!("/usr/local/bin/{binary}")),
        std::path::PathBuf::from(format!("/usr/bin/{binary}")),
        std::path::PathBuf::from(format!("/bin/{binary}")),
        // fnm (Fast Node Manager)
        std::path::PathBuf::from(format!(
            "{home}/.local/share/fnm/aliases/default/bin/{binary}"
        )),
        // asdf
        std::path::PathBuf::from(format!("{home}/.asdf/shims/{binary}")),
        // volta
        std::path::PathBuf::from(format!("{home}/.volta/bin/{binary}")),
        // npm global (system node, no version manager)
        std::path::PathBuf::from(format!("{home}/.npm-global/bin/{binary}")),
    ];

    // nvm: ~/.nvm/versions/node/*/bin/{binary}
    let nvm_versions = std::path::PathBuf::from(format!("{home}/.nvm/versions/node"));
    if let Ok(entries) = std::fs::read_dir(nvm_versions) {
        paths.extend(
            entries
                .flatten()
                .map(|entry| entry.path().join("bin").join(binary)),
        );
    }

    // nodenv: ~/.nodenv/versions/*/bin/{binary}
    let nodenv_versions = std::path::PathBuf::from(format!("{home}/.nodenv/versions"));
    if let Ok(entries) = std::fs::read_dir(nodenv_versions) {
        paths.extend(
            entries
                .flatten()
                .map(|entry| entry.path().join("bin").join(binary)),
        );
    }

    paths
}

fn find_binary_path(binary: &str) -> Option<String> {
    resolve_binary(binary).map(|p| p.to_string_lossy().to_string())
}

fn verify_binary(path: &str) -> bool {
    Command::new(path)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

fn binary_exists(binary: &str) -> bool {
    // Find the binary first, then verify it's the expected agent.
    if let Some(path) = find_binary_path(binary) {
        if verify_binary(&path) {
            return true;
        }
    }
    // Fall back to interactive login shell PATH lookup.
    shell_command_succeeds("command", &["-v", binary])
}

fn agent_installed(name: &str, binaries: &[&str], app_paths: &[&str]) -> bool {
    binaries.iter().any(|bin| binary_exists(bin))
        || app_paths
            .iter()
            .any(|path| std::path::Path::new(path).exists())
        || (name == "github-copilot-cli" && command_succeeds("gh", &["copilot", "--help"]))
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
    if data
        .get("mcpServers")
        .and_then(|s| s.get("mnemo"))
        .is_some_and(|server| {
            !server
                .get("disabled")
                .and_then(|disabled| disabled.as_bool())
                .unwrap_or(false)
        })
    {
        return true;
    }

    // Check project-level: projects.*.mcpServers.mnemo (Claude Code stores per-project configs)
    if let Some(projects) = data.get("projects").and_then(|p| p.as_object()) {
        for (_proj_path, proj_val) in projects {
            if proj_val
                .get("mcpServers")
                .and_then(|s| s.get("mnemo"))
                .is_some_and(|server| {
                    !server
                        .get("disabled")
                        .and_then(|disabled| disabled.as_bool())
                        .unwrap_or(false)
                })
            {
                return true;
            }
        }
    }

    false
}

fn check_prompt_injected(prompt_path: &str) -> bool {
    std::fs::read_to_string(prompt_path)
        .map(|content| {
            content.contains("<!-- mnemo-start -->")
                && content.contains("<!-- mnemo-end -->")
        })
        .unwrap_or(false)
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

            // Start the WS Interface Gateway (port 8788)
            init_interface_gateway();

            tauri::async_runtime::spawn_blocking(|| {
                // Sync bundled CLI to system install path before starting server.
                // Ensures the server runs the latest version bundled with the app.
                sync_cli_binary();
                let _ = ensure_mnemo_server_running();
            });

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
                        stop_mnemo_server();
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
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                stop_mnemo_server();
            }
        });
}
