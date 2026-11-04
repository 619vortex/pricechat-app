//! Kiwi VPN's privileged side. `config` and `protocol` are plain logic with tests. `windows` holds everything that touches the operating system.

pub mod config;
pub mod protocol;
#[cfg(windows)]
pub mod windows;
