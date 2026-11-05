# Changes from the official source

Official source: https://git.zx2c4.com/wireguard-apple at 2fec12a6e1f6e3460b6ee483aa00ad29cddadab1 (tag 1.0.16-27). Only `Package.swift`, `COPYING` and the three `Sources/WireGuardKit*` folders are kept. Nothing else is changed but these two:

1. `Package.swift`: tools version 5.5 instead of 5.3. The manifest names iOS 15 and macOS 12, which 5.3 can't, so Swift Package Manager reports an invalid manifest.
2. `Sources/WireGuardKitC/WireGuardKitC.h`: adds `#include <sys/types.h>`. Xcode 16 and later refuse `u_int32_t` and friends without it. It's the change proposed in the project's own pull request 33.
