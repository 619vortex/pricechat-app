//! Kiwi VPN's privileged side. `config`, `guard`, `protocol` and `update` are plain logic with tests. `windows` holds everything that touches the operating system.

pub mod config;
pub mod guard;
pub mod protocol;
pub mod update;
#[cfg(windows)]
pub mod firewall;
#[cfg(windows)]
pub mod update_win;
#[cfg(windows)]
pub mod windows;
