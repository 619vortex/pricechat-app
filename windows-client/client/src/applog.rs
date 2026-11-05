//! The app's own record of what happened when someone pressed Connect or Disconnect, on this PC only. Callers pass states and error messages, never a config, key or token.

use std::io::Write;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

const LIMIT: u64 = 256 * 1024;

/// Never fails anyone's connection: a log that can't be written is simply not written.
pub fn write(path: &Path, line: &str) {
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if std::fs::metadata(path).map(|m| m.len() > LIMIT).unwrap_or(false) {
        let _ = std::fs::rename(path, path.with_extension("old.log"));
    }
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
        let _ = writeln!(file, "{now} {line}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_appends_and_keeps_only_one_old_file() {
        let mut raw = [0u8; 8];
        getrandom::getrandom(&mut raw).unwrap();
        let dir = std::env::temp_dir().join(format!("kiwi-log-{}", u64::from_le_bytes(raw)));
        let path = dir.join("kiwivpn.log");
        write(&path, "one");
        write(&path, "two");
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.contains(" one\n") && text.contains(" two\n"));
        std::fs::write(&path, vec![b'x'; LIMIT as usize + 1]).unwrap();
        write(&path, "three");
        assert!(std::fs::read_to_string(&path).unwrap().contains(" three\n"));
        assert!(path.with_extension("old.log").exists());
        assert!(std::fs::metadata(&path).unwrap().len() < LIMIT);
        let _ = std::fs::remove_dir_all(dir);
    }
}
