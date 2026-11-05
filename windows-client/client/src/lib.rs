//! Kiwi VPN's client logic. `auth` signs in through the browser, `keys` makes the device's key, `api` talks to Price Chat, `store` keeps secrets with Windows DPAPI, and
//! `flow` puts them together: sign in, choose a server, get a config. The private key is made here, stays on this PC, and reaches the service only over the checked pipe.

pub mod api;
pub mod applog;
pub mod auth;
pub mod flow;
pub mod keys;
#[cfg(windows)]
pub mod opener;
pub mod settings;
#[cfg(windows)]
pub mod store;
