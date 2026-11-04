// No console window behind the app in a release build.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod ipc;

use kiwiclient::api::{Account, Backend, Http, BASE};
use kiwiclient::flow::{self, Vault};
use kiwiclient::store::FileVault;
use kiwiclient::{auth, opener};
use kiwivpn::protocol::{Request, State, Status};
use serde::Serialize;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

static CANCEL_SIGN_IN: AtomicBool = AtomicBool::new(false);

/// What the window is told. `service` is false when the service can't be reached, and then there's no tunnel state to report: the window never guesses one.
#[derive(Serialize)]
struct Answer {
    service: bool,
    status: Option<Status>,
    message: Option<String>,
}

fn answer(result: Result<Status, String>) -> Answer {
    match result {
        Ok(status) => Answer { service: true, status: Some(status), message: None },
        Err(message) => Answer { service: false, status: None, message: Some(message) },
    }
}

fn backend() -> Result<Http, String> {
    Http::new(BASE)
}

#[tauri::command(async)]
fn vpn_status() -> Answer {
    answer(ipc::ask(&Request::Status))
}

#[tauri::command(async)]
fn vpn_disconnect() -> Answer {
    answer(ipc::ask(&Request::Disconnect))
}

/// Everything is decided here, not in the page: the page names a server, and the key and the config never reach it.
#[tauri::command(async)]
fn vpn_connect(server_id: i64) -> Result<Answer, String> {
    let computer = std::env::var("COMPUTERNAME").unwrap_or_default();
    let config = flow::config_for(&backend()?, &FileVault::open()?, &computer, server_id)?;
    Ok(answer(ipc::ask(&Request::Connect { config: config.to_string() })))
}

/// Advanced Settings, for testing: connect from a config file. It's read here, so its key still never enters the page. Compiled out of a build made with --no-default-features.
#[cfg(feature = "test-config")]
#[tauri::command(async)]
fn vpn_connect_test_file(app: tauri::AppHandle) -> Result<Option<Answer>, String> {
    use tauri_plugin_dialog::DialogExt;
    let Some(picked) = app.dialog().file().add_filter("WireGuard config", &["conf"]).blocking_pick_file() else { return Ok(None) };
    let path = picked.into_path().map_err(|e| e.to_string())?;
    let config = std::fs::read_to_string(&path).map_err(|e| format!("Can't read that file: {e}"))?;
    Ok(Some(answer(ipc::ask(&Request::Connect { config }))))
}

/// True in a build that has the config file option, so Advanced Settings can show it.
#[tauri::command]
fn has_test_config() -> bool {
    cfg!(feature = "test-config")
}

#[derive(Serialize)]
struct AccountView {
    signed_in: bool,
    account: Option<Account>,
    suggested: Option<i64>,
    last_server: Option<i64>,
    error: Option<String>,
}

#[tauri::command(async)]
fn account_state() -> Result<AccountView, String> {
    let vault = FileVault::open()?;
    let last_server = vault.last_server();
    let empty = |signed_in, error| AccountView { signed_in, account: None, suggested: None, last_server, error };
    let Some(token) = vault.session() else { return Ok(empty(false, None)) };
    match backend()?.account(&token) {
        Ok(account) => {
            let suggested = flow::suggested(&account.servers, last_server);
            Ok(AccountView { signed_in: true, account: Some(account), suggested, last_server, error: None })
        }
        Err(e) if e.signed_out() => {
            vault.forget_all();
            Ok(empty(false, None))
        }
        Err(e) => Ok(empty(true, Some(e.message))),
    }
}

#[tauri::command(async)]
fn sign_in() -> Result<(), String> {
    CANCEL_SIGN_IN.store(false, Ordering::SeqCst);
    let attempt = auth::Attempt::new()?;
    let (listener, port) = auth::listen()?;
    opener::open_in_browser(&attempt.page(BASE, port))?;
    let code = auth::wait_for_code(&listener, &attempt.state, &CANCEL_SIGN_IN, Duration::from_secs(auth::SIGN_IN_MINUTES * 60))?;
    let token = backend()?.token(&code, &attempt.verifier).map_err(|e| e.message)?;
    FileVault::open()?.save_session(&token)
}

#[tauri::command]
fn cancel_sign_in() {
    CANCEL_SIGN_IN.store(true, Ordering::SeqCst);
}

/// Closes the tunnel, ends the session on the server, and forgets everything on this PC. The device stays listed under My Devices until the next sign-in replaces it.
#[tauri::command(async)]
fn sign_out() -> Result<(), String> {
    if let Ok(status) = ipc::ask(&Request::Status) {
        if status.state != State::Disconnected {
            let _ = ipc::ask(&Request::Disconnect);
        }
    }
    let vault = FileVault::open()?;
    if let Some(token) = vault.session() {
        let _ = backend()?.logout(&token);
    }
    vault.forget_all();
    Ok(())
}

#[tauri::command(async)]
fn start_trial() -> Result<(), String> {
    let vault = FileVault::open()?;
    let token = vault.session().ok_or(flow::SIGNED_OUT)?;
    backend()?.start_trial(&token).map(|_| ()).map_err(|e| e.message)
}

/// The address the outside world sees, from Price Chat's own endpoint.
#[tauri::command(async)]
fn my_ip() -> Result<String, String> {
    backend()?.my_ip().map_err(|e| e.message)
}

/// Only these two pages: the window can't send the browser anywhere else.
#[tauri::command]
fn open_site(page: String) -> Result<(), String> {
    match page.as_str() {
        "plans" => opener::open_in_browser(&format!("{BASE}/vpn#vpn-plans")),
        "devices" => opener::open_in_browser(&format!("{BASE}/vpn/devices")),
        _ => Err("Unknown page.".into()),
    }
}

fn main() {
    // A program that starts this app could otherwise use these to open the window's browser to remote control, or swap the browser itself.
    for name in ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", "WEBVIEW2_USER_DATA_FOLDER", "WEBVIEW2_RELEASE_CHANNEL_PREFERENCE", "WEBVIEW2_PIPE_FOR_SCRIPT_DEBUGGER"] {
        std::env::remove_var(name);
    }
    let builder = tauri::Builder::default();
    #[cfg(feature = "test-config")]
    let builder = builder.plugin(tauri_plugin_dialog::init()).invoke_handler(tauri::generate_handler![vpn_status, vpn_disconnect, vpn_connect, vpn_connect_test_file, has_test_config, account_state, sign_in, cancel_sign_in, sign_out, start_trial, my_ip, open_site]);
    #[cfg(not(feature = "test-config"))]
    let builder = builder.invoke_handler(tauri::generate_handler![vpn_status, vpn_disconnect, vpn_connect, has_test_config, account_state, sign_in, cancel_sign_in, sign_out, start_trial, my_ip, open_site]);
    builder.run(tauri::generate_context!()).expect("Kiwi VPN couldn't start");
}
