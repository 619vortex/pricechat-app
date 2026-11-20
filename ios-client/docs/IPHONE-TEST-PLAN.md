# iPhone test plan

**Status when written: nothing here has been run.** Don't mark a line passed unless it was done on a real iPhone. Record the iPhone model, iOS version, and the build (Settings). A Simulator can't run any of this.

**Before you start:** run `./tools/build-mac.sh` and send me its result: logic tests come first.

| # | Test | Pass means |
|---|---|---|
| 1 | Install and open | The declaration screen comes first. Continue, then Welcome |
| 2 | Sign in by email | Sheet opens; after Allow, it closes by itself and shows your name. **Note exactly what happens at the end:** see `SIGN-IN.md` |
| 3 | Sign out, then sign in with Google | Same. Note if iOS asks about Local Network |
| 4 | Account with no trial: press Start free trial. Then one that started on Windows or Android | First starts it; second shows the same days left as the website, with no new trial. Signing in alone starts nothing |
| 5 | First Connect to Germany | iOS asks to add a VPN configuration. Allow. "Protected" appears only after that |
| 6 | Decline that prompt, on a fresh install | A clear message, and Connect works when pressed again |
| 7 | Exit address at an outside site in Safari | Germany's server, matching the app's "verified" line |
| 8 | Switch to Finland while connected | Finland's address, without pressing anything else |
| 9 | Disconnect, then reconnect straight away. Then 20 times, switching countries | Works every time. Your own address is back while off. **Note any failure's message** |
| 10 | DNS and IPv6: a leak test site, and an IPv6 test site, with the VPN on, on a network with IPv6 | Only the server's resolver. No real IPv6 address of yours. **This decides what can be claimed** |
| 11 | Custom DNS, then a custom MTU | The DNS test shows it, and it reconnects to apply. A bad value is refused |
| 12 | Wi-Fi to mobile data and back, while connected | Recovers by itself. Note the time, and whether it showed Reconnecting |
| 13 | Airplane mode for 30 s, then 5 minutes | Recovers. Traffic doesn't leave outside the tunnel |
| 14 | Send the app to the background for 10 minutes, with the phone locked | Still connected |
| 15 | Force quit the app while connected, then reopen | The tunnel kept running. It reads Protected, and the timer counts from the original start |
| 16 | Reboot the iPhone, with On-Demand off | VPN is off, and the app says Unprotected |
| 17 | On-Demand: Always, then reboot | Connects by itself after the first unlock. Note if it does before unlock |
| 18 | On-Demand: On Wi-Fi, moving on and off Wi-Fi | On only on Wi-Fi |
| 19 | On-Demand on, then press Disconnect | Stays off, and On-Demand is now Off, as Settings says it will be |
| 20 | Send all traffic on, then stop the server being reachable | **Record what reaches the internet.** It isn't a claimed kill switch |
| 21 | Local network devices with Send all traffic on, then with Allow local network off | The setting makes the difference it says |
| 22 | Shortcuts: run Connect, Disconnect, and an automation | They work, and the app shows the true state afterwards |
| 23 | Turn the VPN off in iOS Settings | The app reads Connection failed or Unprotected, never Protected |
| 24 | Another VPN app connects | Same |
| 25 | Device limit: use up devices elsewhere, then connect | A clear message; Remove under Settings works. Check that another phone's device isn't touched |
| 26 | Expired account | The website's status, no connection |
| 27 | End the session on the website, then use the app | Asks to sign in again, and doesn't crash |
| 28 | Passport, once the shared API is deployed | XP, level and invite link match the website. **Until then it shows a link, which is expected** |
| 29 | Sign out | Devices gone from My Devices, the VPN configuration gone from iOS Settings, and the session gone |
| 30 | Console.app on a Mac, filtered to the app, during 5 to 9 | No key, token or config in the log |

**Send back:** iPhone and iOS version, the build, each result, and any on-screen message.
