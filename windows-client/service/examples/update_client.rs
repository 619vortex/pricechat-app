//! The CI update test's hands (ci/update-test/run.ps1): asks the installed service about updates as an administrator would, and
//! checks the kill switch after an update. Not part of the installer.
//!   update_client check | install | status | disconnect   prints the service's answer as one JSON line (exit 1 if it can't ask)
//!   update_client connect-nowhere                         connects to a server that doesn't exist, kill switch on
//!   update_client online                                  exit 0 if a TCP connection to 1.1.1.1:443 opens within 3 s

#[cfg(windows)]
fn main() {
    use kiwivpn::protocol::{Protect, Request};
    use kiwivpn::windows::ask_service;
    use std::net::{SocketAddr, TcpStream};
    use std::time::Duration;

    /// TEST-NET-1: the tunnel runs and never gets an answer (the same config as guard_check's).
    const NOWHERE: &str = "[Interface]\nPrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=\nAddress = 10.255.255.2/32\nDNS = 10.255.255.1\n\n[Peer]\nPublicKey = xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=\nEndpoint = 192.0.2.1:51820\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n";

    let ask = |r: &Request| -> i32 {
        match ask_service(r) {
            Ok(s) => {
                println!("{}", serde_json::to_string(&s).unwrap_or_default());
                0
            }
            Err(e) => {
                println!("{{\"ask_error\":{}}}", serde_json::to_string(&e.message()).unwrap_or_default());
                1
            }
        }
    };
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["check"] => ask(&Request::UpdateCheck),
        ["install"] => ask(&Request::UpdateInstall),
        ["status"] => ask(&Request::Status),
        ["disconnect"] => ask(&Request::Disconnect),
        ["connect-nowhere"] => ask(&Request::Connect { config: NOWHERE.into(), protect: Protect { kill_switch: true, allow_lan: true, ..Default::default() } }),
        ["online"] => {
            let addr: SocketAddr = "1.1.1.1:443".parse().unwrap();
            i32::from(TcpStream::connect_timeout(&addr, Duration::from_secs(3)).is_err())
        }
        _ => {
            eprintln!("usage: update_client check | install | status | disconnect | connect-nowhere | online");
            2
        }
    };
    std::process::exit(code);
}

#[cfg(not(windows))]
fn main() {}
