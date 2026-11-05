# Building Kiwi VPN for Windows

**Rust and Tauri compile only in the Hyper-V VM.** Never on the main PC: Smart App Control blocks unsigned compiler output there, it stays on, and no Kiwi service may run beside `client1`. Don't run `cargo` on the main PC, even for tests or checks.

| Main PC | VM |
|---|---|
| Source code, backend and website work and their tests, deploys, making ZIPs, the window's `npm test` | All Rust: tests, the service, the app, the installer. Installing and connecting |

## The steps
1. **Main PC:** commit, then run `prepare-vm-build.ps1`. It makes `C:\AI\pc-wt\kiwi-vm-build-<commit>.zip` and prints the VM's commands.
2. **VM:** run those commands in an administrator PowerShell. `build-app-in-vm.ps1` runs the Rust tests and builds the public installer without the config file testing option.
3. **Back to the main PC:** with the VM's window in Enhanced Session and the C: drive shared, the script copies `KiwiVPN-Setup.exe`, its `.sha256` and `BUILD-INFO.txt` to `\\tsclient\C\AI\pc-wt\kiwi-release` and checks the copy. Otherwise it says what to copy by hand. To turn on drive sharing: when connecting to the VM choose Show Options > Local Resources > More, then tick C:.
4. **Test in the VM:** install it from File Explorer, sign in, connect.
5. **Main PC:** run `receive-installer.ps1`. It checks the hash and which commit it came from, and never runs the installer.

The installer isn't code signed, so it would itself meet Smart App Control if run on the main PC. It doesn't need to be: this PC only hosts it.
