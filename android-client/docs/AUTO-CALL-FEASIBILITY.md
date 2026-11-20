# Auto-Call VPN: feasibility

**Verdict: buildable, but it can't be called reliable until it's been tested on real phones, and it has a limit no code can remove.** The logic is written and has automated tests. No part of it has run against a real WhatsApp or Telegram call.

## The limit that matters most
**It reacts after a call has begun.** A notification appears only once the call is already being set up, and by then the apps may have exchanged network addresses with the other person. So Auto-Call VPN can't promise that the start of a call is protected. Anyone who needs that should connect before the call, or use Always-on VPN. The app says this beside the toggle.

**Turning a VPN on during a call changes the network under it.** The call may blip or drop. Only testing can show how often. This is the biggest risk to the feature being useful at all.

## What Android officially offers
| Mechanism | Works for this? | Notes |
|---|---|---|
| `NotificationListenerService` | **Yes, the only general route** | Needs the person to grant Notification access. It sees every app's notifications, so it's sensitive |
| `Notification.CATEGORY_CALL`, `CallStyle`, `EXTRA_CALL_TYPE` | Yes, if the apps use them | Public. `EXTRA_CALL_TYPE` is public from API 34 |
| `onNotificationRemoved(…, reason)` | Yes | `REASON_APP_CANCEL` means the app removed it. `REASON_CANCEL` means the person swiped it |
| `AudioManager.getMode()` | Supporting only | No permission needed. Shows *some* app is in a call, not which |
| `TelephonyManager` | No | Sees phone-network calls, not WhatsApp or Telegram |
| `TelecomManager` | No | Only sees calls that apps register with the system |
| WhatsApp or Telegram APIs | No | Neither has a public call-state API for other apps |
| Accessibility Service | **Rejected** | It would see the screen. You ruled it out, and Google Play restricts it |

## What couldn't be verified here
**What these two apps actually send.** They don't document it, and it can change with any update, or differ by version and phone maker. So the classifier uses only Android's public signals, in order of strength, and **Settings > Connection diagnostics > What Auto-Call VPN saw** shows what it received: category, ongoing flag, call type, style, channel name, and how it was classified. It shows no names, numbers or messages, and it stays in memory. Those lines are what real-device testing should send back.

Telegram is the bigger unknown: its ongoing-call notification may not set a call category. A weak fallback (an ongoing notification on a channel named for calls) catches that, and it's marked as "phase unknown" so it can't claim a call is active.

**Telling ringing from an active call** works only if the app uses `CallStyle`. Otherwise it reads as unknown, which changes nothing about when the VPN connects: it connects at the first sign of a call. An outgoing call may report "ongoing" while it's still ringing.

## Installing from an APK: an obstacle
Android 13 and later treat Notification access as a **restricted setting** for apps not installed by an app store. The person has to open the app's page, use the menu, and choose **Allow restricted settings**. Android 15 tightened this, and reports differ on whether every phone still offers the way through. **This may block Auto-Call testing on some phones until the app comes from Google Play.** The app explains the steps, but it can't get around this and won't try.

## How each of your requirements is handled
| Requirement | How | Automated test |
|---|---|---|
| 3. Consent | A disclosure dialog needing "I agree", then Android's own settings | Device only |
| 4. Incoming and outgoing, ringing or active | Classifier, where the apps signal it | Yes, for the logic |
| 5. Uses existing VPN permission | `VpnService.prepare` must already be null. If not, it says to open the app | Device only |
| 6. Keeps the previous state | Only ends a VPN it started: `owned` | Yes |
| 7. 15 seconds after a confirmed end | `deadline` | Yes |
| 8. Never while another call is active | Tracks all calls, plus audio mode | Yes |
| 9. Uncertain means stay on and notify | User swipe, listener lost, restart | Yes |
| 10. Missed, rejected, crash, removal, lock, background | See below | Logic yes; behaviour device only |
| 11. On-device only | Only allow-listed apps are read, no title or text, no network code in the listener, nothing saved except `owned` | Partly: by construction |
| 12. Toggle and per-app choice | Settings | Device only |
| 13. Status messages | The engine's statuses, in a notification and in Settings | Yes |
| 14. No repeated starts | Tracks keys, and ignores repeats | Yes |

**Requirement 10 in detail:**
- **Missed or rejected:** the app removes its ringing notification, which counts as a confirmed end. A separate "missed call" notification isn't a call.
- **App crash:** the saved `owned` flag says the VPN was started automatically, with no call to explain it. So it's reported as uncertain and left on. A grace period interrupted by a crash goes the same way.
- **Notification removed by the person:** uncertain, and the VPN stays on.
- **The calling app crashing:** Android may report this as the app cancelling, which would read as a confirmed end. Needs testing. The audio-mode check gives partial protection.
- **Phone locked:** notifications still arrive. Needs testing.
- **Background limits:** the listener is held by the system, and Android exempts VPN apps from the limit on starting a foreground service. Phone makers' battery managers are the risk. Needs testing, particularly on Samsung, Xiaomi and Huawei.

**"Uncertain" stays until the person acts,** because nothing can prove the call is over. That can leave a VPN on longer than needed. You asked for that trade.

## Google Play
See `GOOGLE-PLAY.md`. Reading notifications needs its own prominent disclosure, and Google may question why a VPN needs it. **This feature could put Play approval at risk**, so consider shipping the first Play version without it.
