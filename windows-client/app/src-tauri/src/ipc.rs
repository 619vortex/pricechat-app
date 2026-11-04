//! The app's end of the pipe. The protocol and the check that the other end really is the service both live in the service's library, so the two sides can't drift apart.

use kiwivpn::protocol::{Request, Status};

/// One request, one answer, over a fresh connection. Blocks: call it off the UI thread.
pub fn ask(request: &Request) -> Result<Status, String> {
    kiwivpn::windows::ask_service(request).map_err(|e| e.message())
}
