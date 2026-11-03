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
