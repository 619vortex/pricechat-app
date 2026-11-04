//! Opens a page in the person's own browser.

#[cfg(windows)]
pub fn open_in_browser(url: &str) -> Result<(), String> {
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
    if !url.starts_with("https://") {
        return Err("That isn't a secure address.".into());
    }
    let wide = |s: &str| s.encode_utf16().chain(std::iter::once(0)).collect::<Vec<u16>>();
    let result = unsafe { ShellExecuteW(null_mut(), wide("open").as_ptr(), wide(url).as_ptr(), null(), null(), SW_SHOWNORMAL) };
    // Windows documents anything above 32 as success
    if result as usize > 32 {
        Ok(())
    } else {
        Err("Couldn't open your browser.".into())
    }
}
