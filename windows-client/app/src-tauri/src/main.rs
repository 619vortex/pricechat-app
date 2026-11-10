// No console window behind the app in a release build.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod ipc;

use kiwiclient::api::{Account, Backend, Http, BASE};
use kiwiclient::flow::{self, Vault};
use kiwiclient::store::FileVault;
use kiwiclient::settings::{self, Settings};
use kiwiclient::{applog, auth, measure, opener, tune};
use kiwivpn::protocol::{Protect, Request, State, Status};
use serde::Serialize;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, WindowEvent};
use tauri_plugin_notification::NotificationExt;

static CANCEL_SIGN_IN: AtomicBool = AtomicBool::new(false);
/// The last tunnel this app asked for: the server's address and the DNS its config uses (for the leak check), and the tunnel's own
/// address (the liveness probe sends from it). Nothing secret.
#[derive(Clone)]
struct LastTunnel {
    server_ip: Option<String>,
    dns: Vec<String>,
    address: Option<std::net::Ipv4Addr>,
    resolvers: Vec<std::net::Ipv4Addr>,
}
static LAST_TUNNEL: std::sync::Mutex<Option<LastTunnel>> = std::sync::Mutex::new(None);

fn forget_tunnel() {
    if let Ok(mut last) = LAST_TUNNEL.lock() {
        *last = None;
    }
}

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

fn settings_path() -> Result<PathBuf, String> {
    let base = std::env::var_os("LOCALAPPDATA").ok_or("Windows didn't say where app data goes.")?;
    Ok(PathBuf::from(base).join("live.pricechat.kiwivpn").join("settings.json"))
}

/// States and error messages only, in the app's own folder. See kiwiclient::applog.
fn note(line: &str) {
    if let Ok(settings) = settings_path() {
        applog::write(&settings.with_file_name("kiwivpn.log"), line);
    }
}

fn note_answer(what: &str, answer: &Answer) {
    match &answer.status {
        Some(status) => note(&format!("{what}: service says {:?} {}", status.state, status.error.as_deref().unwrap_or(""))),
        None => note(&format!("{what}: no service: {}", answer.message.as_deref().unwrap_or(""))),
    }
}

fn current_settings() -> Settings {
    settings_path().map(|p| settings::load(&p)).unwrap_or_default()
}

fn started_by_windows() -> bool {
    std::env::args().any(|a| a == settings::STARTED_BY_WINDOWS)
}

fn show_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

#[tauri::command]
fn get_settings() -> Settings {
    let mut saved = current_settings();
    saved.launch_at_startup = settings::startup::is_set(settings::startup::RUN_KEY); // Windows' own Startup Apps page can change this
    saved
}

#[tauri::command]
fn set_settings(new: Settings) -> Result<Settings, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let command = settings::startup_command(&exe.display().to_string());
    settings::startup::set(settings::startup::RUN_KEY, new.launch_at_startup.then_some(command.as_str()))?;
    settings::save(&settings_path()?, &new)?;
    Ok(new)
}

/// True once per launch, only if Windows started the app and the person chose auto-connect. Never a retry: it can't turn into a reconnect loop.
#[tauri::command]
fn should_auto_connect() -> bool {
    static ASKED: AtomicBool = AtomicBool::new(false);
    !ASKED.swap(true, Ordering::SeqCst) && started_by_windows() && current_settings().auto_connect
}

#[derive(Serialize)]
struct Diagnostics {
    version: &'static str,
    service: String,
    tunnel: String,
    handshake_age_seconds: Option<u64>,
    settings_file: String,
    /// What this build can really do. "kill_switch" is listed only when the installed service says it enforces it.
    features: Vec<String>,
}

/// Nothing secret: no keys, tokens or configs.
#[tauri::command(async)]
fn diagnostics() -> Diagnostics {
    let mut features: Vec<String> = ["smart_connect", "failover", "split_tunnel", "block_malware", "speed_test", "leak_check"].iter().map(|f| f.to_string()).collect();
    let (service, tunnel, handshake_age_seconds) = match ipc::ask(&Request::Status) {
        Ok(s) => {
            features.extend(s.features.iter().cloned());
            ("Running".to_string(), format!("{:?}", s.state), s.handshake_age_seconds)
        }
        Err(e) => (e, "Unknown".to_string(), None),
    };
    Diagnostics { version: env!("CARGO_PKG_VERSION"), service, tunnel, handshake_age_seconds, settings_file: settings_path().map(|p| p.display().to_string()).unwrap_or_default(), features }
}

/// A fixed set of messages, so the page can't use this to show anything else.
#[tauri::command]
fn notify(app: AppHandle, kind: String, place: Option<String>) {
    if !current_settings().notifications {
        return;
    }
    let body = match kind.as_str() {
        "connected" => match place {
            Some(p) if p.len() <= 64 => format!("Connected: {p}"),
            _ => "Connected.".to_string(),
        },
        "lost" => "The connection dropped.".to_string(),
        _ => return,
    };
    let _ = app.notification().builder().title("Kiwi VPN").body(body).show();
}

#[tauri::command(async)]
fn vpn_status() -> Answer {
    answer(ipc::ask(&Request::Status))
}

#[tauri::command(async)]
fn vpn_disconnect() -> Answer {
    note("disconnect: pressed");
    forget_tunnel();
    let done = answer(ipc::ask(&Request::Disconnect));
    note_answer("disconnect", &done);
    done
}

/// Everything is decided here, not in the page: the page names a server, and the key and the config never reach it.
/// `pause_first`: failover's switch (2.1). The liveness probe found the server gone while the service still reads Connected (the
/// handshake isn't old enough yet), so the tunnel is paused whatever the service's state says.
#[tauri::command(async)]
fn vpn_connect(server_id: i64, pause_first: Option<bool>) -> Result<Answer, String> {
    let computer = std::env::var("COMPUTERNAME").unwrap_or_default();
    note(&format!("connect: pressed, server {server_id}{}", if pause_first == Some(true) { " (failover)" } else { "" }));
    // a tunnel that's up but not answering still has this PC's routes, so Price Chat can't be reached through it: the service closes it
    // first (keeping the kill switch on), and the next server's settings are fetched directly
    let state = ipc::ask(&Request::Status).map(|s| s.state);
    if matches!(state, Ok(State::Connecting)) || (pause_first == Some(true) && matches!(state, Ok(State::Connected | State::Connecting | State::Failed))) {
        note("connect: the current tunnel isn't answering, pausing it to switch");
        forget_tunnel();
        let _ = ipc::ask(&Request::Pause);
    }
    let config = flow::config_with_retry(&backend()?, &FileVault::open()?, &computer, server_id, &|| {
        note("connect: Price Chat unreachable, trying again");
        std::thread::sleep(Duration::from_millis(1500));
    })
    .map_err(|e| {
        note(&format!("connect: no config: {e}"));
        e
    })?;
    // 2.0 options: split tunneling and the DNS choice, checked again here (the page checked them too).
    let prefs = current_settings();
    let dns: Option<&[&str]> = if prefs.block_malware { Some(&tune::QUAD9) } else { None };
    let mut keep: Vec<tune::Cidr> = tune::dns_of(&config).iter().filter_map(|d| tune::parse_cidr(d)).collect();
    keep.extend(tune::QUAD9.iter().filter_map(|d| tune::parse_cidr(d)));
    let exclude = tune::checked_exclusions(&prefs.split_exclude, &keep)?;
    let config = tune::apply(&config, &exclude, dns)?;
    let server_ip = config.lines().find_map(|l| l.trim().strip_prefix("Endpoint = ")).and_then(|e| e.rsplit_once(':')).map(|(h, _)| h.trim_matches(['[', ']']).to_string());
    if let Ok(mut last) = LAST_TUNNEL.lock() {
        *last = Some(LastTunnel { server_ip, dns: tune::dns_of(&config), address: tune::address_v4_of(&config), resolvers: tune::dns_v4_of(&config) });
    }
    note(&format!("connect: config ready (split ranges {}, malware DNS {}), asking the service", exclude.len(), prefs.block_malware));
    // the kill switch and what it lets through: the service checks the ranges again and enforces it
    let protect = Protect {
        kill_switch: prefs.kill_switch,
        allow_lan: prefs.allow_lan,
        allow: exclude.iter().map(|c| format!("{}/{}", std::net::Ipv4Addr::from(c.ip), c.bits)).collect(),
    };
    let done = answer(ipc::ask(&Request::Connect { config: config.to_string(), protect }));
    note_answer("connect", &done);
    Ok(done)
}

/// Advanced Settings, for testing: connect from a config file. It's read here, so its key still never enters the page. Compiled out of a build made with --no-default-features.
#[cfg(feature = "test-config")]
#[tauri::command(async)]
fn vpn_connect_test_file(app: tauri::AppHandle) -> Result<Option<Answer>, String> {
    use tauri_plugin_dialog::DialogExt;
    let Some(picked) = app.dialog().file().add_filter("WireGuard config", &["conf"]).blocking_pick_file() else { return Ok(None) };
    let path = picked.into_path().map_err(|e| e.to_string())?;
    let config = std::fs::read_to_string(&path).map_err(|e| format!("Can't read that file: {e}"))?;
    Ok(Some(answer(ipc::ask(&Request::connect(config)))))
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
    /// 2.1: servers this PC already has a working device for (failover tries them first). Ids only.
    known_servers: Vec<i64>,
}

#[tauri::command(async)]
fn account_state() -> Result<AccountView, String> {
    let vault = FileVault::open()?;
    let last_server = vault.last_server();
    if let Ok(http) = backend() {
        flow::refresh_if_due(&http, &vault, SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs());
    }
    let empty = |signed_in, error| AccountView { signed_in, account: None, suggested: None, last_server, error, known_servers: Vec::new() };
    let Some(token) = vault.session() else { return Ok(empty(false, None)) };
    match flow::account_starting_trial(&backend()?, &token) {
        Ok(account) => {
            let suggested = flow::suggested(&account.servers, last_server);
            let known_servers = flow::known_servers(&account, &vault);
            Ok(AccountView { signed_in: true, account: Some(account), suggested, last_server, error: None, known_servers })
        }
        Err(e) if e.signed_out() => {
            vault.forget_all();
            Ok(empty(false, None))
        }
        Err(e) => Ok(empty(true, Some(e.message))),
    }
}

/// Smart Connect's timings, all servers at once, run while disconnected: each online server's proxy (3 HTTPS requests) when the backend
/// names one, else its endpoint (3 TCP connections to the proxy port) when this PC has had a config for it. A server this PC has a
/// device for but whose endpoint it hasn't seen yet has it looked up (reading a device's settings creates nothing). The rest go by load.
#[tauri::command(async)]
fn probe_servers() -> Result<std::collections::HashMap<i64, Option<f64>>, String> {
    let vault = FileVault::open()?;
    let token = vault.session().ok_or("Sign in first.")?;
    let http = backend()?;
    let account = http.account(&token).map_err(|e| e.message)?;
    let mut targets: Vec<(i64, measure::Target)> = Vec::new();
    for s in account.servers.iter().filter(|s| s.online) {
        let mut endpoint = vault.endpoint(s.id);
        if s.proxy_host.is_none() && endpoint.is_none() {
            if let Some(id) = vault.device_id(s.id).filter(|id| account.devices.iter().any(|d| d.id == *id)) {
                if let Ok(peer) = http.peer(&token, id) {
                    vault.save_endpoint(s.id, &peer.endpoint_host);
                    endpoint = vault.endpoint(s.id);
                }
            }
        }
        targets.push((s.id, measure::Target { proxy: s.proxy_host.clone(), endpoint }));
    }
    Ok(measure::probe_targets(&targets).into_iter().collect())
}

/// One liveness probe through the tunnel this app started (see measure::tunnel_answers). None when it can't tell: no tunnel started
/// by this run of the app, or a config without an IPv4 address or resolver. The window never counts None as a failure.
#[tauri::command(async)]
fn probe_tunnel() -> Option<bool> {
    let last = LAST_TUNNEL.lock().ok().and_then(|l| l.clone())?;
    let address = last.address?;
    if last.resolvers.is_empty() {
        return None;
    }
    let alive = measure::tunnel_answers(address, &last.resolvers);
    if !alive {
        note("liveness: no answer through the tunnel");
    }
    Some(alive)
}

#[tauri::command(async)]
fn speed_test() -> Result<measure::SpeedReport, String> {
    note("speed test: started");
    measure::speed_test()
}

#[tauri::command(async)]
fn leak_check() -> Result<measure::LeakReport, String> {
    let (server_ip, dns) = LAST_TUNNEL.lock().ok().and_then(|l| l.clone()).map(|t| (t.server_ip, t.dns)).unwrap_or((None, Vec::new()));
    measure::leak_check(server_ip, &dns)
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
    forget_tunnel();
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
    // Opening the app again brings up the one that's running, so there's never a second window asking the service for different things.
    let builder = tauri::Builder::default().plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_window(app))).plugin(tauri_plugin_notification::init());
    #[cfg(feature = "test-config")]
    let builder = builder.plugin(tauri_plugin_dialog::init()).invoke_handler(tauri::generate_handler![vpn_status, vpn_disconnect, vpn_connect, vpn_connect_test_file, has_test_config, account_state, sign_in, cancel_sign_in, sign_out, start_trial, my_ip, open_site, get_settings, set_settings, should_auto_connect, diagnostics, notify, probe_servers, probe_tunnel, speed_test, leak_check]);
    #[cfg(not(feature = "test-config"))]
    let builder = builder.invoke_handler(tauri::generate_handler![vpn_status, vpn_disconnect, vpn_connect, has_test_config, account_state, sign_in, cancel_sign_in, sign_out, start_trial, my_ip, open_site, get_settings, set_settings, should_auto_connect, diagnostics, notify, probe_servers, probe_tunnel, speed_test, leak_check]);
    builder
        .setup(|app| {
            let open = MenuItem::with_id(app, "open", "Open Kiwi VPN", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            let mut tray = TrayIconBuilder::new().tooltip("Kiwi VPN").menu(&menu).show_menu_on_left_click(false);
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.on_menu_event(|app, event| match event.id.as_ref() {
                "open" => show_window(app),
                "quit" => app.exit(0), // the tunnel belongs to the service, so quitting the window doesn't drop it
                _ => {}
            })
            .on_tray_icon_event(|tray, event| {
                if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                    show_window(tray.app_handle());
                }
            })
            .build(app)?;
            if started_by_windows() && current_settings().minimize_to_tray {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if current_settings().minimize_to_tray {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("Kiwi VPN couldn't start");
}
