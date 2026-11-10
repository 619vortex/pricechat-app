import { PageHeader } from "../components/ui";
import { TelegramTradingCard } from "./TelegramTradingCard";

/** Admin → Telegram → Telegram trading: members trading Hyperliquid from the Telegram bot. Its own page, first in the
 *  Telegram area, so the owner finds the mainnet switch and the emergency stop at once. */
export function TelegramTradingPage() {
  return (
    <>
      <PageHeader title="TELEGRAM TRADING" subtitle="Members trading on Hyperliquid from the Telegram bot: the mainnet switch, the emergency stop, referral codes and what's happening now." />
      <TelegramTradingCard />
    </>
  );
}
