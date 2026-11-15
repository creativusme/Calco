// Calco for Windows: a native window around the UI, and the engine that does the work.
//
// The installer ships a complete runtime next to the app (runtime/: Python with the engine and its
// libraries, COLMAP, OpenMVS). The app starts it when the window opens and stops it, with every
// tool it launched, when the window closes. If an engine is already listening on 7878 (for example
// one started from a source checkout with Start Calco.cmd), that one is used instead.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{Manager, RunEvent};

struct Engine(Mutex<Option<Child>>);

/// The engine a source checkout recorded in %LOCALAPPDATA%\Calco\engine.json (developer setup).
fn recorded_engine() -> Option<(PathBuf, Option<PathBuf>)> {
    let base = std::env::var("LOCALAPPDATA").ok()?;
    let text = std::fs::read_to_string(PathBuf::from(base).join("Calco").join("engine.json")).ok()?;
    let json: serde_json::Value = serde_json::from_str(&text).ok()?;
    let python = PathBuf::from(json.get("python")?.as_str()?);
    let cwd = json.get("cwd").and_then(|v| v.as_str()).map(PathBuf::from);
    Some((python, cwd))
}

fn start_engine(runtime: Option<PathBuf>) -> Option<Child> {
    if std::net::TcpStream::connect("127.0.0.1:7878").is_ok() {
        return None; // already running
    }
    let bundled = runtime.filter(|r| r.join("python").join("python.exe").exists());
    let mut cmd = match &bundled {
        Some(home) => {
            let mut c = Command::new(home.join("python").join("python.exe"));
            c.env("CALCO_HOME", home).env("PYTHONNOUSERSITE", "1").current_dir(home);
            c
        }
        None => {
            let (python, cwd) = match std::env::var("CALCO_PYTHON") {
                Ok(p) => (PathBuf::from(p), None),
                Err(_) => recorded_engine().unwrap_or_else(|| (PathBuf::from("python"), None)),
            };
            let mut c = Command::new(python);
            if let Some(dir) = cwd {
                c.current_dir(dir);
            }
            c
        }
    };
    cmd.args(["-m", "calco", "serve"]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd.spawn().ok()
}

/// Stop the engine and whatever it launched (COLMAP, OpenMVS, a splat trainer).
fn stop_engine(mut child: Child) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .creation_flags(0x0800_0000)
            .status();
    }
    let _ = child.kill();
}

fn main() {
    tauri::Builder::default()
        .manage(Engine(Mutex::new(None)))
        .setup(|app| {
            let runtime = app.path().resource_dir().ok().map(|d| d.join("runtime"));
            *app.state::<Engine>().0.lock().unwrap() = start_engine(runtime);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Calco")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(child) = app.state::<Engine>().0.lock().unwrap().take() {
                    stop_engine(child);
                }
            }
        });
}
