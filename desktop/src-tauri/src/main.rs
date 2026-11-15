// Calco desktop shell: a native window around the web UI. It starts the Python engine
// (`calco serve`) if it is not already running and stops it again on exit.
//
// Which Python: CALCO_PYTHON if set, else the one the engine recorded in
// %LOCALAPPDATA%\Calco\engine.json the last time it ran (Start Calco.cmd creates it), else `python`.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{Manager, RunEvent};

struct Engine(Mutex<Option<Child>>);

fn recorded_engine() -> Option<(String, Option<PathBuf>)> {
    let base = std::env::var("LOCALAPPDATA").ok()?;
    let text = std::fs::read_to_string(PathBuf::from(base).join("Calco").join("engine.json")).ok()?;
    let json: serde_json::Value = serde_json::from_str(&text).ok()?;
    let python = json.get("python")?.as_str()?.to_string();
    let cwd = json.get("cwd").and_then(|v| v.as_str()).map(PathBuf::from);
    Some((python, cwd))
}

fn start_engine() -> Option<Child> {
    if std::net::TcpStream::connect("127.0.0.1:7878").is_ok() {
        return None; // already running (Start Calco.cmd, or by hand during development)
    }
    let (python, cwd) = match std::env::var("CALCO_PYTHON") {
        Ok(p) => (p, None),
        Err(_) => recorded_engine().unwrap_or_else(|| ("python".into(), None)),
    };
    let mut cmd = Command::new(python);
    cmd.args(["-m", "calco", "serve"]);
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd.spawn().ok()
}

fn main() {
    tauri::Builder::default()
        .manage(Engine(Mutex::new(start_engine())))
        .build(tauri::generate_context!())
        .expect("error while building Calco")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) = app.state::<Engine>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
