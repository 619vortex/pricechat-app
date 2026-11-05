# App-Based VPN

Settings > App-Based VPN. The person picks one of three modes, and none is forced.

| Mode | On the VPN | Android call |
|---|---|---|
| All Apps (default) | Everything | Neither list |
| Only Selected Apps | The apps switched on, plus Kiwi VPN itself | `addAllowedApplication` |
| All Apps Except Selected | Everything except the apps switched off | `addDisallowedApplication` |

**How it reaches the real interface.** `RoutingPolicy.applyTo` writes `IncludedApplications` or `ExcludedApplications` into the `[Interface]` section. The WireGuard library parses that and calls the two Android methods above while building the interface. Only one line is ever written, so the lists can't be mixed.

## Decisions worth knowing
- **The switch always means Use VPN.** On uses Kiwi VPN and off uses the normal connection, in both modes, so its meaning never reverses. Each row also says "Uses VPN" or "Normal connection". The stored lists do differ per mode, and only the code sees that.
- **An empty Only Selected is refused.** Android's rule: "When the allowed list is empty, all apps use the VPN", the opposite of what the person chose. So Save is disabled, and if every selected app has since been uninstalled, Connect refuses and says why. An empty All Except is allowed and simply means all apps.
- **Kiwi VPN itself is always on the VPN and isn't listed.** Android applies these lists to this app too. Otherwise its exit-address check would go around the tunnel and report the wrong address. It sends nothing else unusual through the tunnel.
- **A mode shows as active only once it's on the interface.** `Link.routing` is set when the handshake succeeds. The settings screen reads "Active now" from there, not from what was last saved.
- **Changing mode while connected rebuilds the interface,** because Android can't change the lists on a running one. New details are fetched first. If that fails, the old tunnel and its mode stay, and the app says the choice wasn't applied. If the rebuild itself fails, it shows Connection failed. In that gap, traffic may briefly use the normal connection, and the prompt says so.
- **Each mode keeps its own list,** so moving between modes loses nothing.
- **"Protected" is qualified** in the notification and on Home whenever not every app is on the VPN.

## Privacy
The app list comes from a narrow MAIN/LAUNCHER query, not `QUERY_ALL_PACKAGES`. The list and the choices stay in the encrypted Vault, excluded from backups. Nothing is sent to Price Chat, and package names never go in the log: only the mode and a count. Choices survive signing out.

## Limits
- **Only apps with an icon are listed.** Background components with none stay off the VPN under Only Selected, and stay on it under All Except.
- **Apps outside the VPN get nothing from it.** Their traffic and DNS use the normal connection.
- **"Block connections without VPN":** Google warns that apps outside the lists "lose their network connection". The screen warns about this. The exact behaviour needs a phone: test 63.
- **Multi-process apps and shared UIDs:** Android applies these rules per app, by UID. Apps sharing a UID move together.
- **Work profiles:** untested, test 68.

## Open risk: the foreground service
Lint says `systemExempted` needs an exact-alarm permission. Android's documentation lists "VPN apps" as another way to qualify, so none was added and lint has a recorded exception. I couldn't confirm this from Android's source. **If Android refuses on 14 or later, `startForeground` throws. The app catches it, but Android may still end the app for not entering the foreground in time, which would stop the tunnel.** Test 47 checks this first. If it fails, the fix is to change the service's type, not to add an alarm permission.

## Auto-Call VPN
It uses the same plan as any other connection, and never changes the selections. If a selected calling app wouldn't use the VPN, the routing screen and the Auto-Call card both say so: the VPN would come up and the call wouldn't use it.

## Windows
Windows isn't affected and has its own networking. `app_routing_enabled` is on only for Android in the capability table, which isn't deployed, and the app treats a missing answer as available.

## Testing status
Unit tests cover the policy, the config it produces and the state machine, using a stand-in for the tunnel. **Nothing proves an app's traffic really takes the right path until `DEVICE-TEST-PLAN.md`, tests 47 to 68, are run on a phone.**
