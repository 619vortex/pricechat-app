# Kiwi VPN design specification

One recognizable product, built natively on each platform. The tokens come from the live code: `frontend/src/website/vpn.css` and `windows-client/app/src/app.css`. Android's copy is `ui/Theme.kt`.

## Colour
| Token | Value | Use |
|---|---|---|
| Ground | `#070B09` | Page background |
| Forest | `#0D1D15` | Cards |
| Forest 2 / 3 | `#12281C` / `#1A3726` | Selected card; idle ring, progress track |
| Line | `rgba(214,245,170,.11)` | Hairlines |
| Lime | `#A3E635` | Primary action, progress |
| Lime bright | `#BEF264` | Protected status, links |
| Ink | `#0B1406` | Text on lime |
| Warn | `#FF9F5A` | Failed, reconnecting, beta label |
| Text / Soft / Muted | `#F6F8F4` / `#BCC7BF` / `#84918A` | Text levels |
| Gold / Gold 2 | `#E6BD5A` / `#F5DD9C` | Website accents and Royal |

## Type
Archivo on the website and Windows, heavy and condensed for headlines. **Android currently uses the system font in heavy weights**, because Archivo isn't bundled. Adding it is a small change, once its licence (OFL) notice is added with the others.

## Status words, the same everywhere
| State | Word | Colour | Only when |
|---|---|---|---|
| Connected | Protected | Lime bright | A real handshake |
| Off | Unprotected | Text | |
| Starting | Connecting | Text | |
| Stopping | Disconnecting | Text | |
| Server stopped answering | Reconnecting | Warn | |
| Failed | Connection failed | Warn | Always with a reason |

**Windows's window currently uses its own wording.** Align it the next time that client changes. "Protected" says the tunnel is up, not that nothing can leak, so the beta warning stays beside it.

## Motion
It moves only to say something true: a slow breath when off, a turning ring while connecting, a steady glow when connected. It honours the system's reduced-motion setting.

## Passport
- **Level:** the rank's name and number, as the website has them.
- **Progress:** a lime bar on Forest 3, from `rank.progress`. Never calculated on a client.
- **XP:** with thousands separators.
- **Character:** the website draws it from `rank.art`. **Android doesn't draw it yet:** it shows the rank by name.
- **Achievements:** badge names. Android has a plain list so far.

## Gaming Mode terms, for when it exists
Latency, jitter, packet loss, measured route. Never "faster", "boost" or "lower ping" without a measurement behind that exact claim.

## Platform differences that are fine
Android has bottom navigation and one large button. Windows has one compact window. The website has long pages. The colours, status words, Passport figures and honesty about what's tested stay the same.
