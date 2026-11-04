//! kiwivpn-service: the Windows service, and a small test client for proving a real tunnel before any app exists.
//!   service | tunnel <file>   started by Windows, never by hand
//!   install | uninstall       needs an administrator
//!   connect <file> | disconnect | status

#[cfg(windows)]
mod app {
    use kiwivpn::protocol::{Request, State};
    use kiwivpn::windows::{self as win, OWN_SERVICE};
    use std::ffi::OsString;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::time::Duration;
    use windows_service::service::{ServiceControl, ServiceControlAccept, ServiceExitCode, ServiceState, ServiceStatus, ServiceType};
    use windows_service::service_control_handler::{self, ServiceControlHandlerResult};
    use windows_service::{define_windows_service, service_dispatcher};

    define_windows_service!(ffi_service_main, service_main);

    fn service_main(_args: Vec<OsString>) {
        let stop = Arc::new(AtomicBool::new(false));
        let flag = Arc::clone(&stop);
        let handler = move |control| match control {
            ServiceControl::Stop => {
                flag.store(true, Ordering::SeqCst);
                win::nudge_pipe();
                ServiceControlHandlerResult::NoError
            }
            ServiceControl::Interrogate => ServiceControlHandlerResult::NoError,
            _ => ServiceControlHandlerResult::NotImplemented,
        };
        let Ok(status) = service_control_handler::register(OWN_SERVICE, handler) else { return };
        let report = |state, accept| {
            let _ = status.set_service_status(ServiceStatus {
                service_type: ServiceType::OWN_PROCESS,
                current_state: state,
                controls_accepted: accept,
                exit_code: ServiceExitCode::Win32(0),
                checkpoint: 0,
                wait_hint: Duration::from_secs(5),
                process_id: None,
            });
        };
        report(ServiceState::Running, ServiceControlAccept::STOP);
        let _ = win::serve(stop);
        report(ServiceState::Stopped, ServiceControlAccept::empty());
    }

    fn show(request: Request) -> i32 {
        match win::ask_service(&request) {
            Ok(s) => {
                println!("state: {:?}", s.state);
                if let Some(age) = s.handshake_age_seconds {
                    println!("last handshake: {age}s ago");
                }
                if let Some(since) = s.connected_since {
                    println!("connected since: {since}");
                }
                println!("received: {} bytes, sent: {} bytes", s.rx_bytes, s.tx_bytes);
                if let Some(e) = &s.error {
                    println!("error: {e}");
                }
                i32::from(s.state == State::Failed)
            }
            Err(e) => {
                eprintln!("{}", e.message());
                1
            }
        }
    }

    pub fn main() -> i32 {
        let args: Vec<String> = std::env::args().skip(1).collect();
        match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
            ["service"] => match service_dispatcher::start(OWN_SERVICE, ffi_service_main) {
                Ok(()) => 0,
                Err(e) => {
                    eprintln!("This runs as a Windows service: {e}");
                    1
                }
            },
            ["tunnel", conf] => match win::run_tunnel(conf) {
                Ok(()) => 0,
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            },
            ["install"] => done(win::install()),
            ["uninstall"] => done(win::uninstall()),
            ["connect", file] => match std::fs::read_to_string(file) {
                Ok(config) => show(Request::Connect { config }),
                Err(e) => {
                    eprintln!("Can't read {file}: {e}");
                    1
                }
            },
            ["disconnect"] => show(Request::Disconnect),
            ["status"] => show(Request::Status),
            _ => {
                eprintln!("usage: kiwivpn-service install | uninstall | connect <config> | disconnect | status");
                2
            }
        }
    }

    fn done(result: Result<(), String>) -> i32 {
        match result {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("{e}");
                1
            }
        }
    }
}

#[cfg(windows)]
fn main() {
    std::process::exit(app::main());
}

#[cfg(not(windows))]
fn main() {
    eprintln!("Kiwi VPN's service only runs on Windows.");
}
