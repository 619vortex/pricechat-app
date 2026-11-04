# Who can control the tunnel on the PC

## The honest limit
Windows doesn't treat two programs run by the same user as separated. A program running as that user can inject code into KiwiVPN.exe, drive its window, or simply kill it. No app can fully stop that without an administrator-level or signed-data check. So the goal is: nothing is reachable by just opening the pipe, the attack gets loud and specific, and the thing that matters most, where traffic goes, is decided by a signature rather than by who asked.

## The layers
| Layer | Stops | Status |
|---|---|---|
| 1. Pipe rule: users get read, write and read-attributes only | A program joining the service's pipe to impersonate it | Implemented |
| 2. Client checks owner and session 0 before sending | A fake service catching a private key | Implemented |
| 3. Service checks the caller: Connect and Disconnect need KiwiVPN.exe from its own admin-only folder, or an administrator. Status stays readable | Any program silently opening the pipe to change or stop the tunnel, including the service's own command-line tool and a renamed copy | Implemented |
| 4. Service validates every config: no scripts, only known fields | A config running commands as SYSTEM | Implemented |
| 5. **Signed server list.** The backend signs each server's public key and endpoint with a key held off the API; the service has the public half built in, and refuses any peer without a valid signature | Even code injected into KiwiVPN.exe pointing the tunnel at an attacker's server | **Proposed for milestone 4. Needs a backend change and your approval** |
| 6. Visible change: the app tells the person whenever the server changes or the tunnel drops without them pressing anything | A silent change going unnoticed | Proposed for milestone 4 |
| 7. Optional kill-switch-style protection and app hardening: code signing, signature check of the caller instead of only its path | Swapped binaries if someone gets past the folder rights | Milestone 7 |

## Privilege escalation review
- Service path is quoted. It now refuses to install outside Program Files, so its files can't sit somewhere a user can write.
- DLLs load by full path from the install folder. Config folder is SYSTEM and Administrators only, and the config is rebuilt from parsed values, never copied.
- Connect and disconnect run one at a time. At most 64 clients are served at once, so endless connections can't exhaust the service. **Remaining:** a program holding 64 idle connections could still block the app's status checks. That's a local nuisance, not an escalation: it gains nothing, and it could kill the app anyway. A read timeout would fix it, and it's worth doing before release.
- `tunnel <file>` on the command line runs as the caller, with no extra rights.

## What this doesn't stop
- Code already running as the user can inject into KiwiVPN.exe, or automate its window, until layer 5 limits what that achieves. Even then it could still disconnect the tunnel, which layer 6 would make visible.
- Malware running as administrator. It's above every boundary here.
- The test build's "choose a config file" button accepts any valid config. It's compiled out of release builds, and the debug test build also allows developer tools.
