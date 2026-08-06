use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::{Manager, RunEvent};

struct ApiSidecar(Mutex<Option<Child>>);

const HEALTH_URL: &str = "http://127.0.0.1:29180/health";

fn wait_for_health(timeout: Duration) -> bool {
    let start = std::time::Instant::now();
    while start.elapsed() < timeout {
        if let Ok(resp) = ureq::get(HEALTH_URL).call() {
            if (200..300).contains(&resp.status()) {
                return true;
            }
        }
        thread::sleep(Duration::from_millis(200));
    }
    false
}

fn api_binary_in(dir: &Path) -> PathBuf {
    let root = dir.join("audit-api");
    if cfg!(windows) {
        root.join("audit-api.exe")
    } else {
        root.join("audit-api")
    }
}

/// Extract bundled `audit-api.tar.gz` into app data once, then return the binary path.
fn ensure_api_binary(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir: {e}"))?;
    let archive = {
        let direct = resource_dir.join("audit-api.tar.gz");
        let nested = resource_dir.join("resources").join("audit-api.tar.gz");
        if direct.exists() {
            direct
        } else if nested.exists() {
            nested
        } else {
            return Err(format!(
                "missing API archive under {} (tried audit-api.tar.gz and resources/audit-api.tar.gz)",
                resource_dir.display()
            ));
        }
    };

    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app_data_dir: {e}"))?;
    let runtime = data_dir.join("audit-api-runtime");
    let marker = runtime.join(".ready");
    let bin = api_binary_in(&runtime);

    if marker.exists() && bin.exists() {
        return Ok(bin);
    }

    let _ = fs::remove_dir_all(&runtime);
    fs::create_dir_all(&runtime).map_err(|e| format!("mkdir runtime: {e}"))?;

    let status = Command::new("tar")
        .arg("-xzf")
        .arg(&archive)
        .arg("-C")
        .arg(&runtime)
        .status()
        .map_err(|e| format!("tar extract: {e}"))?;
    if !status.success() {
        return Err(format!("tar extract failed: {status}"));
    }

    if !bin.exists() {
        return Err(format!("API binary missing after extract: {}", bin.display()));
    }

    #[cfg(target_os = "macos")]
    {
        // Soften Gatekeeper first-launch stalls on nested unsigned libs.
        let _ = Command::new("codesign")
            .args(["--force", "--sign", "-", "--timestamp=none"])
            .arg(&bin)
            .status();
    }

    fs::write(&marker, b"1").map_err(|e| format!("write marker: {e}"))?;
    Ok(bin)
}

fn spawn_api_sidecar(app: &tauri::AppHandle) -> Result<(), String> {
    let bin = ensure_api_binary(app)?;
    let workdir = bin
        .parent()
        .ok_or_else(|| "API binary has no parent dir".to_string())?
        .to_path_buf();

    let mut child = Command::new(&bin)
        .current_dir(&workdir)
        .env("AUDIT_API_HOST", "127.0.0.1")
        .env("AUDIT_API_PORT", "29180")
        .env("NO_PROXY", "*")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn {}: {e}", bin.display()))?;

    if let Some(stdout) = child.stdout.take() {
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines().flatten() {
                eprintln!("[audit-api] {line}");
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines().flatten() {
                eprintln!("[audit-api:err] {line}");
            }
        });
    }

    {
        let state = app.state::<ApiSidecar>();
        let mut guard = state.0.lock().map_err(|e| e.to_string())?;
        *guard = Some(child);
    }
    Ok(())
}

fn kill_api_sidecar(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<ApiSidecar>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
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
            // Never block window creation on API boot.
            let handle = app.handle().clone();
            thread::spawn(move || match spawn_api_sidecar(&handle) {
                Ok(()) => {
                    if wait_for_health(Duration::from_secs(90)) {
                        eprintln!("API sidecar ready on port 29180");
                    } else {
                        eprintln!("API sidecar did not become healthy within 90s");
                    }
                }
                Err(err) => eprintln!("failed to start API sidecar: {err}"),
            });
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
