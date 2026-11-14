// Calco desktop shell: a native window around the web UI. It starts the Python engine
// (`calco serve`) if it is not already running and stops it again on exit.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{Manager, RunEvent};

struct Engine(Mutex<Option<Child>>);

fn start_engine() -> Option<Child> {
    if std::net::TcpStream::connect("127.0.0.1:7878").is_ok() {
        return None; // already running (e.g. started by hand during development)
    }
    // CALCO_PYTHON points at the engine's interpreter; the default relies on `calco` being installed.
    let python = std::env::var("CALCO_PYTHON").unwrap_or_else(|_| "python".into());
    let mut cmd = Command::new(python);
    cmd.args(["-m", "calco", "serve"]);
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
