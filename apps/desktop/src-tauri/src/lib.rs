use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

struct ApiSidecar(Mutex<Option<CommandChild>>);

const API_PORT: &str = "29180";
const HEALTH_URL: &str = "http://127.0.0.1:29180/health";

fn wait_for_health(timeout: Duration) -> bool {
    let start = std::time::Instant::now();
    while start.elapsed() < timeout {
        if let Ok(resp) = ureq::get(HEALTH_URL).call() {
            if resp.status() >= 200 && resp.status() < 300 {
                return true;
            }
        }
        thread::sleep(Duration::from_millis(200));
    }
    false
}

fn spawn_api_sidecar(app: &tauri::AppHandle) -> Result<(), String> {
    // Defaults in audit_api.__main__ already bind 127.0.0.1:29180.
    let sidecar = app
        .shell()
        .sidecar("audit-api")
        .map_err(|e| format!("locate audit-api sidecar: {e}"))?;

    let (mut rx, child) = sidecar
        .spawn()
        .map_err(|e| format!("spawn audit-api sidecar: {e}"))?;

    {
        let state = app.state::<ApiSidecar>();
        let mut guard = state.0.lock().map_err(|e| e.to_string())?;
        *guard = Some(child);
    }

    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                tauri_plugin_shell::process::CommandEvent::Stdout(line) => {
                    let text = String::from_utf8_lossy(&line);
                    eprintln!("[audit-api] {text}");
                }
                tauri_plugin_shell::process::CommandEvent::Stderr(line) => {
                    let text = String::from_utf8_lossy(&line);
                    eprintln!("[audit-api:err] {text}");
                }
                tauri_plugin_shell::process::CommandEvent::Terminated(payload) => {
                    eprintln!("[audit-api] terminated: {payload:?}");
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(())
}

fn kill_api_sidecar(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<ApiSidecar>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(child) = guard.take() {
                let _ = child.kill();
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(ApiSidecar(Mutex::new(None)))
        .setup(|app| {
            if let Err(err) = spawn_api_sidecar(app.handle()) {
                eprintln!("failed to start API sidecar: {err}");
            } else if !wait_for_health(Duration::from_secs(45)) {
                eprintln!("API sidecar did not become healthy within 45s");
            } else {
                eprintln!("API sidecar ready on port {API_PORT}");
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                kill_api_sidecar(app_handle);
            }
        });
}
