import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { StructurePanel, type SLayer, type Structure } from "./structure";

const base = { unit: "USD", fetched_at: Date.UTC(2026, 9, 20, 10, 0), notes: [] as string[] };
const data: Structure = {
  coin: "BTC", timeframe: "1h", layers: {
    oi: { ...base, kind: "open_interest", basis: "aggregated", basis_label: "Aggregated", title: "BTC open interest", venues: ["Binance", "OKX"],
      skipped: { Bybit: "blocks requests from this server's region" }, total_usd: 2e10, by_venue: { Binance: 1.2e10, OKX: 8e9 },
      history: [[1, 1e10], [2, 1.1e10]], history_venues: ["Binance", "OKX"], period: "1h", history_skipped: {} },
    zones: { ...base, kind: "liquidation_zones", basis: "estimate", basis_label: "Model estimate", title: "zones", venues: ["Binance"], skipped: {},
      price: 100, bins: [], top: [{ px: 95, px_hi: 95.25, side: "long", usd: 5e7 }], period: "1h",
      assumptions: ["Model estimate from Binance open-interest changes (1h), not confirmed positions", "Leverage mix assumed: 10x 30%"] },
    twap: { error: "Binance blocks requests from this server's region" },
  },
};

afterEach(cleanup);

describe("Market structure panel", () => {
  it("labels every layer by basis, names missing venues and never draws a failed layer", () => {
    render(<StructurePanel d={data} on={new Set<SLayer>(["oi", "zones", "twap"])} err={null} />);
    expect(screen.getByText("Aggregated")).toBeTruthy();
    expect(screen.getByText("Model estimate")).toBeTruthy();
    expect(screen.getByText(/not confirmed positions/)).toBeTruthy();
    expect(screen.getByText(/Bybit \(blocks requests/)).toBeTruthy();
    expect(screen.getByText(/Unavailable: Binance blocks requests/)).toBeTruthy();
    expect(screen.getByText("$20.00B")).toBeTruthy();
  });

  it("renders nothing when no layer is on", () => {
    const { container } = render(<StructurePanel d={data} on={new Set()} err={null} />);
    expect(container.innerHTML).toBe("");
  });
});
