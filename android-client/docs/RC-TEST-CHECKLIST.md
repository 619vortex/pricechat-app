# Release-candidate checklist for a physical phone

**Nothing here has been run on a phone by Claude.** Mark a line only after doing it on a real phone. Record the phone model, Android version, and the app's version and build from Settings.

The earlier `DEVICE-TEST-PLAN.md` has more detail. This is what has to pass before the APK goes on the website.

## Before you start
1. **In the old debug app:** press Sign out. It removes its devices from your account. The debug app and this one are separate apps, and this one can't find the debug app's devices: each stays until it's removed, and an account has 3.
2. Uninstall the debug app. Two VPN apps side by side make results hard to read.
3. Check `pricechat.live/vpn/devices`, and remove any device left over from testing.
4. Copy the APK to the phone. Check its SHA-256 if it passed through anything you don't trust.
5. Open it. Android will ask you to allow installing from that source.

**Expect:** a Play Protect warning about an unknown app is normal for an APK outside Google Play. Read it, and report the exact wording, since customers will see it too.

## A. It's a real release build
| ☐ | Test | Pass means |
|---|---|---|
| ☐ | A1. Open the app | Starts with no crash. This is the first real run of the shrunk code: **R8 problems show up here** |
| ☐ | A2. Settings | Shows version `0.1.0-rc1` and its build. Diagnostics has **no** "Run 20 cycles" button |
| ☐ | A3. Sign in with email, then with Google | Returns to the app, and shows your name and plan. This proves JSON still works after shrinking |

## B. Android 14 and later: foreground service. **Do this first on the newest phone you have**
Android's documentation allows VPN apps this service type, and Android's lint tool disagrees, so only a real phone settles it.

| ☐ | Test | Pass means |
|---|---|---|
| ☐ | B1. First Connect, accept the VPN prompt, and allow notifications | Connects, with no crash |
| ☐ | B2. With notifications **denied**, Connect | Still connects, with no crash |
| ☐ | B3. Wait 2 minutes on the Home screen | Still running. An "app isn't responding" or a crash about the foreground service is a **failure**: send the Android version and the time |
| ☐ | B4. Pull down the notification | Says Protected and the country, and its Disconnect button works |
| ☐ | B5. Lock the phone for 15 minutes, then check | Still connected |
| ☐ | B6. Swipe the app away from Recents | Tunnel keeps running, or the app says it was lost when reopened. Never a false Protected |
| ☐ | B7. Settings > Apps > Force stop | VPN ends, and the app says so when reopened |
| ☐ | B8. Battery saver on, then Connect | Connects |
| ☐ | B9. Android 15 and 16, if available: repeat B1 and B3 | Same |

Note which Android versions you covered. An untested version stays untested.

## C. Connection
| ☐ | Test | Pass means |
|---|---|---|
| ☐ | C1. Connect to Germany. Check an outside site such as ipleak.net in Chrome | "Protected" only once connected. Germany's address |
| ☐ | C2. Switch to Finland while connected | Finland's address, with nothing else pressed |
| ☐ | C3. Disconnect | Unprotected, no key icon, your own address back |
| ☐ | C4. Reconnect straight away, with no pause | Connects. This is the bug Windows 0.1.0 had |
| ☐ | C5. Do 20 connect and disconnect rounds by hand, switching countries | All work. The debug build's automatic version isn't in this build |
| ☐ | C6. Wi-Fi to mobile data and back, while connected | Recovers by itself. Note how long it took |
| ☐ | C7. Airplane mode for 30 seconds, then for 5 minutes | Recovers |
| ☐ | C8. Reboot, then open the app | Off, and says so |

## D. App-Based VPN: real traffic routing
Use Chrome and ipleak.net for each. **"VPN" means the server's address. "Own" means your normal one.** Reconnect isn't needed by hand: the app does it after Save.

| ☐ | Mode | Chrome's switch | Pass means |
|---|---|---|---|
| ☐ | D1. All Apps | No switches | VPN |
| ☐ | D2. Only Selected Apps | On | VPN |
| ☐ | D3. Only Selected Apps | Off, with another app on | Own |
| ☐ | D4. All Apps Except Selected | On | VPN |
| ☐ | D5. All Apps Except Selected | Off | Own |

| ☐ | Test | Pass means |
|---|---|---|
| ☐ | D6. Check a second app in each mode, if it shows your address | Follows its own switch |
| ☐ | D7. Only Selected with nothing on | Save is disabled, with the reason shown |
| ☐ | D8. Only Selected, uninstall the only selected app, then Connect | Refuses and says why. **It must not route every app** |
| ☐ | D9. Change mode while connected | Warns, reconnects, and "Active now" changes only afterwards |
| ☐ | D10. Move between modes | Each mode's app list is as you left it |
| ☐ | D11. Restart the app, then reboot | The mode and lists are kept |
| ☐ | D12. Germany to Finland, in each mode | The mode stays |
| ☐ | D13. Home and the notification, in Only Selected | They say only the selected apps are covered, not simply "Protected" |

## E. Device identity
| ☐ | Test | Pass means |
|---|---|---|
| ☐ | E1. After connecting, open `pricechat.live/vpn/devices` | Named like `Android Pixel8 a1b2c3d4 (2)`, with no plain ID |
| ☐ | E2. Uninstall, reinstall, sign in, and Connect | Connects. **The old device is replaced, and the count doesn't grow** |
| ☐ | E3. **Two phones of the same model, if you can, on one account** | Each keeps working, and neither removes the other's device. This is the bug that was fixed |
| ☐ | E4. Use up your devices elsewhere, then Connect | A clear message. Account > Remove fixes it |
| ☐ | E5. Sign out | This phone's devices leave My Devices |

## F. Security
**These decide what may be claimed. A passed connection test claims nothing here.**

| ☐ | Test | Pass means |
|---|---|---|
| ☐ | F1. DNS leak test in Chrome, on the VPN | Only the server's resolver |
| ☐ | F2. Same, with Android Private DNS on | Record what appears |
| ☐ | F3. IPv6 test on a network with IPv6, on the VPN | No real IPv6 address of yours |
| ☐ | F4. Always-on VPN on, then reboot | Connects by itself |
| ☐ | F5. Always-on plus Block connections without VPN, then make the server unreachable | Nothing reaches the internet. **Record it. Only this could support any claim about blocking** |
| ☐ | F6. Block on, with an app switched off in All Except | Record what happens to it. Google warns it can lose its connection |
| ☐ | F7. `adb logcat -s KiwiVPN`, if you can | No key, token, config or address |

## G. Auto-Call VPN, if you'll ship it
Notification access may need **Allow restricted settings**, on the app's page in Settings. Note if your Android version doesn't offer that.

| ☐ | Test | Pass means |
|---|---|---|
| ☐ | G1. WhatsApp, then Telegram: an incoming call, answered | VPN comes up. **Record whether the call survives it** |
| ☐ | G2. Off 15 seconds after the call ends | Yes |
| ☐ | G3. VPN already on by hand, then a call | Stays on afterwards |

**Decide after this:** if G1 interrupts calls, the feature should stay labelled experimental or be left out.

## H. Update path: **check this before publishing**
| ☐ | Test | Pass means |
|---|---|---|
| ☐ | H1. With this RC installed and signed in, install a later build signed with the same key | Installs over it, and stays signed in with its devices kept |

## Send back
Phone model, Android version, each result, the exact text of any error, and the time of any crash. If it crashes, the private mapping file beside the APK lets me read the report.

## Only after this passes
Upload it, set `ANDROID_BETA` with `testBuild: false`, and verify the live hash. **That's a separate step needing your approval.**
