import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useApi } from "./useApi";

let hidden = false;
Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
afterEach(() => { cleanup(); vi.useRealTimers(); hidden = false; });

function Probe({ fetcher }: { fetcher: () => Promise<number> }) {
  const r = useApi(fetcher, 1000);
  return <p>{r.data ?? "-"}</p>;
}

describe("useApi polling", () => {
  it("pauses while the tab is hidden and refreshes at once when it's visible again", async () => {
    vi.useFakeTimers();
    let n = 0;
    const fetcher = vi.fn(async () => ++n);
    render(<Probe fetcher={fetcher} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(fetcher).toHaveBeenCalledTimes(3);  // visible: polls
    hidden = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetcher).toHaveBeenCalledTimes(3);  // hidden: nothing
    hidden = false;
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(0); });
    expect(fetcher).toHaveBeenCalledTimes(4);  // back: refreshed immediately
  });
});
