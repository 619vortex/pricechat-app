// No console window behind the app in a release build.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod ipc;

use kiwivpn::protocol::{Request, Status};
use serde::Serialize;

/// What the window is told. `service` is false when the service can't be reached, and then there's no tunnel state to report: the window never guesses one.
#[derive(Serialize)]
struct Answer {
    service: bool,
    status: Option<Status>,
    message: Option<String>,
}

fn answer(result: Result<Status, ipc::IpcError>) -> Answer {
    match result {
        Ok(status) => Answer { service: true, status: Some(status), message: None },
        Err(e) => Answer { service: false, status: None, message: Some(e.message()) },
    }
}

#[tauri::command(async)]
fn vpn_status() -> Answer {
    answer(ipc::ask(&Request::Status))
}

#[tauri::command(async)]
fn vpn_disconnect() -> Answer {
    answer(ipc::ask(&Request::Disconnect))
}

/// The test build's connect: the person picks a config file. It's read here, so the private key passes straight to the service and never enters the web page.
#[cfg(feature = "test-config")]
#[tauri::command(async)]
fn vpn_connect_test_file(app: tauri::AppHandle) -> Option<Answer> {
    use tauri_plugin_dialog::DialogExt;
    let picked = app.dialog().file().add_filter("WireGuard config", &["conf"]).blocking_pick_file()?;
    let path = match picked.into_path() {
        Ok(p) => p,
        Err(e) => return Some(failed(e.to_string())),
    };
    match std::fs::read_to_string(&path) {
        Ok(config) => Some(answer(ipc::ask(&Request::Connect { config }))),
        Err(e) => Some(failed(format!("Can't read that file: {e}"))),
    }
}

#[cfg(feature = "test-config")]
fn failed(error: String) -> Answer {
    Answer { service: true, status: Some(Status::failed(error)), message: None }
}

/// True in a build that has the config file button, so the window can show it.
#[tauri::command]
fn test_build() -> bool {
    cfg!(feature = "test-config")
}

fn main() {
    let builder = tauri::Builder::default();
    #[cfg(feature = "test-config")]
    let builder = builder.plugin(tauri_plugin_dialog::init()).invoke_handler(tauri::generate_handler![vpn_status, vpn_disconnect, vpn_connect_test_file, test_build]);
    #[cfg(not(feature = "test-config"))]
    let builder = builder.invoke_handler(tauri::generate_handler![vpn_status, vpn_disconnect, test_build]);
    builder.run(tauri::generate_context!()).expect("Kiwi VPN couldn't start");
}
