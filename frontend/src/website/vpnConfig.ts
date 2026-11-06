/** Official listing addresses, filled in only once a store has approved the extension: an empty one shows Coming Soon, never a link.
 *  brave: the Chrome listing, once the extension has actually been tested in Brave. firefox: needs its own Firefox extension first. */
export const STORE_LINKS: { chrome: string; edge: string; brave: string; firefox: string } = { chrome: "https://chromewebstore.google.com/detail/kiwi-vpn/jeadaadhacdikcbahliklcaaeaddeloa", edge: "", brave: "", firefox: "" };

/** A store button only ever gets the store's own listing address, in its official form with the extension id: Chrome
 *  https://chromewebstore.google.com/detail/<slug>/<id>, Edge https://microsoftedge.microsoft.com/addons/detail/<slug>/<id>.
 *  Anything else (another site, a search page, a missing or malformed id) leaves the extension on Coming Soon. */
export function storeLink(store: "chrome" | "edge", href: string): string | null {
  const form = store === "chrome"
    ? /^https:\/\/chromewebstore\.google\.com\/detail\/[a-z0-9][a-z0-9-]*\/[a-p]{32}$/
    : /^https:\/\/microsoftedge\.microsoft\.com\/addons\/detail\/[a-z0-9][a-z0-9-]*\/[a-z]{32}$/;
  return form.test(href) ? href : null;
}

/** What the server charges and how long a trial lasts, for visitors while payments are off: the public API only sends prices once payments are on. A test fails if these
 *  stop matching backend/app/vpn/billing.py (PRICE_USD, PERIOD_DAYS) and service.py (TRIAL_DAYS, KEEP_LAPSED). When the server does send billing, its figures are used. */
export const PLAN = { price_usd: 9.99, period_days: 30, trial_days: 30, keep_days: 30 };

/** The Windows beta: the button appears only while this names a hosted installer. All three together, from the build. Clear them and nothing on the page offers a download. */
export const WINDOWS_BETA: { href: string; version: string; sha256: string } = { href: "/downloads/KiwiVPN-Setup.exe", version: "0.1.1", sha256: "077AE0F651C5BE3F8DB6FDD2FDBA4C94344A7F963CDBADBAC3D1747829F40598" };

/** The Android APK. Fill `href` and `sha256` together, and a test ties them to the file. Clear them and the button goes back to disabled.
 *  `testing`: true while real-phone testing is unfinished, so the card says so plainly rather than presenting a verified release. Set it false only once that testing has passed. */
export const ANDROID_BETA: { href: string; version: string; sha256: string; testing: boolean } = { href: "/downloads/KiwiVPN-0.1.0-rc1.apk", version: "0.1.0-rc1", sha256: "CB52F93919F40BA9E14FFFAD1D80417FB48446F56A182071C59B7B9987C706C7", testing: true };

/** A download button only ever gets a file hosted on this site: never a local path, another site or a page. Anything else leaves the button disabled. */
export function hostedDownload(href: string): string | null {
  return /^\/downloads\/[A-Za-z0-9][A-Za-z0-9._-]*\.(exe|apk)$/.test(href) ? href : null;
}

export type VisitorBrowser = "chrome" | "edge" | "phone" | "other";

/** Which install button to put first. Edge's and Opera's agents also say Chrome, so they're ruled out first. */
export function visitorBrowser(agent: string): VisitorBrowser {
  if (/Android|iPhone|iPad|Mobile/i.test(agent)) return "phone";
  if (/\bEdg\//.test(agent)) return "edge";
  if (/\b(OPR|Vivaldi|YaBrowser|SamsungBrowser)\//.test(agent)) return "other";
  return /\bChrome\//.test(agent) ? "chrome" : "other";
}

export type VisitorOs = "Windows" | "macOS" | "Linux" | "Android" | "iOS" | null;

/** Which device to mark "Your device" in the downloads. iPads can call themselves Macs, but then they also report touch, which a page checks with the second argument. */
export function visitorOs(agent: string, touch = 0): VisitorOs {
  if (/Android/i.test(agent)) return "Android";
  if (/iPhone|iPad|iPod/i.test(agent)) return "iOS";
  if (/Windows NT/i.test(agent)) return "Windows";
  if (/Mac OS X|Macintosh/i.test(agent)) return touch > 1 ? "iOS" : "macOS";
  return /Linux|X11/i.test(agent) ? "Linux" : null;
}

/** A device's WireGuard config, shown once: its QR code and its .conf download. Used by the member page and by Admin. */

export async function configQr(config: string): Promise<string> {
  const Q = await import("qrcode");
  const svg = await Q.default.toString(config, { type: "svg", margin: 1, errorCorrectionLevel: "M", color: { dark: "#05120c", light: "#ffffff" } });
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export function saveConfig(config: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([config], { type: "application/octet-stream" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
