//! The person's choices, in their own AppData, so they survive upgrades. Nothing secret lives here. A missing or damaged file just means the defaults.

use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub launch_at_startup: bool,
    pub auto_connect: bool,
    pub minimize_to_tray: bool,
    pub notifications: bool,
    // 2.0. `#[serde(default)]` on the struct: a 0.1 file without these loads with the defaults below.
    /// Connect picks the fastest server that isn't busy.
    pub smart_connect: bool,
    /// Retry, then move to the next best server, when the tunnel stops answering.
    pub failover: bool,
    /// Ask the service to block traffic outside the tunnel while connected (only if the service supports it).
    pub kill_switch: bool,
    /// With the kill switch on, still allow the local network (printers, the router).
    pub allow_lan: bool,
    /// 2.2: the strict kill switch (audit M3). The block also holds while the service restarts, at boot and through every automatic
    /// give-up; only Disconnect or `kiwivpn-service unblock` lifts it. Needs `kill_switch`. Off by default.
    pub strict_kill_switch: bool,
    /// 2.2: with the kill switch on, block DNS over TLS (port 853) outside the tunnel too (audit L14). Off by default.
    pub block_dot: bool,
    /// Quad9's malware-blocking DNS instead of the server's resolver.
    pub block_malware: bool,
    /// IPv4 ranges that skip the tunnel (split tunneling), checked again when connecting.
    pub split_exclude: Vec<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            launch_at_startup: false,
            auto_connect: false,
            minimize_to_tray: false,
            notifications: true,
            smart_connect: true,
            failover: true,
            kill_switch: false,
            allow_lan: true,
            strict_kill_switch: false,
            block_dot: false,
            block_malware: false,
            split_exclude: Vec::new(),
        }
    }
}

pub fn load(path: &Path) -> Settings {
    std::fs::read_to_string(path).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default()
}

pub fn save(path: &Path, settings: &Settings) -> Result<(), String> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("Couldn't save settings: {e}"))?;
    }
    let text = serde_json::to_string_pretty(settings).map_err(|e| e.to_string())?;
    std::fs::write(path, text).map_err(|e| format!("Couldn't save settings: {e}"))
}

/// What Windows starts at sign-in. The flag tells the app it wasn't opened by hand.
pub const STARTED_BY_WINDOWS: &str = "--autostart";

pub fn startup_command(exe: &str) -> String {
    format!("\"{exe}\" {STARTED_BY_WINDOWS}")
}

#[cfg(windows)]
pub mod startup {
    //! The ordinary per-user "run at sign-in" entry. It needs no administrator rights, and it's the person's to change in Windows' own Startup Apps settings.
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::System::Registry::{RegCloseKey, RegCreateKeyExW, RegDeleteValueW, RegQueryValueExW, RegSetValueExW, HKEY, HKEY_CURRENT_USER, KEY_READ, KEY_WRITE, REG_OPTION_NON_VOLATILE, REG_SZ};

    pub const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
    pub const VALUE: &str = "KiwiVPN";

    fn wide(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(std::iter::once(0)).collect()
    }

    fn open(subkey: &str) -> Result<HKEY, String> {
        let mut key: HKEY = null_mut();
        let got = unsafe { RegCreateKeyExW(HKEY_CURRENT_USER, wide(subkey).as_ptr(), 0, null(), REG_OPTION_NON_VOLATILE, KEY_READ | KEY_WRITE, null(), &mut key, null_mut()) };
        if got != 0 {
            return Err(format!("Couldn't change the startup setting (Windows error {got})."));
        }
        Ok(key)
    }

    /// Some(command) adds or replaces the entry, None removes it.
    pub fn set(subkey: &str, command: Option<&str>) -> Result<(), String> {
        let key = open(subkey)?;
        let result = match command {
            Some(text) => {
                let data = wide(text);
                unsafe { RegSetValueExW(key, wide(VALUE).as_ptr(), 0, REG_SZ, data.as_ptr() as *const u8, (data.len() * 2) as u32) }
            }
            None => match unsafe { RegDeleteValueW(key, wide(VALUE).as_ptr()) } {
                2 => 0, // already gone
                other => other,
            },
        };
        unsafe { RegCloseKey(key) };
        if result != 0 {
            return Err(format!("Couldn't change the startup setting (Windows error {result})."));
        }
        Ok(())
    }

    pub fn is_set(subkey: &str) -> bool {
        let Ok(key) = open(subkey) else { return false };
        let got = unsafe { RegQueryValueExW(key, wide(VALUE).as_ptr(), null(), null_mut(), null_mut(), null_mut()) };
        unsafe { RegCloseKey(key) };
        got == 0
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn adds_finds_and_removes_the_entry_in_a_test_key() {
            let key = r"Software\KiwiVPN-Test";
            set(key, None).unwrap();
            assert!(!is_set(key));
            set(key, Some(r#""C:\Program Files\KiwiVPN\KiwiVPN.exe" --autostart"#)).unwrap();
            assert!(is_set(key));
            set(key, None).unwrap();
            assert!(!is_set(key));
            set(key, None).unwrap();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_or_damaged_settings_mean_the_defaults_and_nothing_starts_by_itself() {
        let d = Settings::default();
        assert!(!d.launch_at_startup && !d.auto_connect && !d.minimize_to_tray && d.notifications);
        let dir = std::env::temp_dir().join(format!("kiwi-settings-{}", std::process::id()));
        let path = dir.join("settings.json");
        assert_eq!(load(&path), d);
        save(&path, &Settings { auto_connect: true, ..Settings::default() }).unwrap();
        assert!(load(&path).auto_connect);
        std::fs::write(&path, "{ not json").unwrap();
        assert_eq!(load(&path), d);
        std::fs::write(&path, r#"{"minimize_to_tray":true,"future_setting":1}"#).unwrap();
        let later = load(&path);
        assert!(later.minimize_to_tray && later.notifications, "a file from another version keeps what it can");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn a_2_1_settings_file_loads_with_the_2_2_options_off() {
        let dir = std::env::temp_dir().join(format!("kiwi-settings-22-{}", std::process::id()));
        let path = dir.join("settings.json");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&path, r#"{"kill_switch":true,"allow_lan":false,"smart_connect":true}"#).unwrap();
        let s = load(&path);
        assert!(s.kill_switch && !s.allow_lan && !s.strict_kill_switch && !s.block_dot, "strict mode and the DNS over TLS block are never on by themselves");
        let d = Settings::default();
        assert!(!d.strict_kill_switch && !d.block_dot);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn the_startup_command_quotes_a_path_with_spaces() {
        assert_eq!(startup_command(r"C:\Program Files\KiwiVPN\KiwiVPN.exe"), r#""C:\Program Files\KiwiVPN\KiwiVPN.exe" --autostart"#);
    }
}
