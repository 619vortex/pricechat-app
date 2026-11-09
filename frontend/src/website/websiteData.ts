/**
 * Price Chat website copy and configuration. Edit this file to change the words or the Kiwi
 * artwork. Content (videos, franchises, trending topics) comes from Price Chat itself; section switches and
 * public links are set in the dashboard (Website page).
 *
 * Images: put files in frontend/public/website/ and reference them as "/website/<file>".
 * A section with nothing real to show is hidden; nothing here is ever filled with placeholder text.
 */

export type Network = "telegram" | "x" | "youtube";

export const site = {
  name: "Price Chat",
  tagline: "Everything has a price.",
  description: "Markets. Money. Culture. Everything has a price. Short stories and conversations from Price Chat.",

  // THE canonical Kiwi. Every Kiwi on the website (hero, logo mark, Our Story) renders this one file;
  // never redraw or substitute it. Source and how it was made: docs/brand/README.md.
  kiwi: { src: "/brand/kiwi.webp", width: 1000, height: 844 },
  shareImage: null as string | null, // 1200x630 image for link previews, e.g. "/website/share.png"
  // Public paths once the site is deployed on its own address (used for canonical / og:url only).
  publicPaths: { home: "/", content: "/content" },

  hero: {
    // Rendered as: EVERYTHING / HAS A PRICE.  (`accent` is shown in Price Chat green)
    line1: "Everything",
    line2: "has",
    accent: "a price.",
    support: ["Markets. Money. Culture.", "Everything has a price."],
  },

  // Section nav. Category links appear only once content is filed under that category (dashboard ›
  // Content › Published), so no link ever leads to an empty page.
  nav: [
    { key: "latest", label: "Latest" },
    { key: "markets", label: "Markets" },
    { key: "crypto", label: "Crypto" },
    { key: "money", label: "Money" },
    { key: "culture", label: "Culture" },
    { key: "about", label: "About" },
  ] as const,

  latest: { title: "Latest", viewAll: "View all", empty: "New videos are on their way." },

  whatsItWorth: {
    title: "What's it worth?",
    intro: "One object. One price. The story behind it.",
  },

  priceBoard: { title: "The Price Board" },

  originals: { title: "Price Chat Originals", intro: "The longer stories." },

  community: {
    title: ["Don't just watch.", "Join the chat."],
    lines: ["Markets move fast.", "The conversation moves faster."],
    // The community sign-up, under the social links
    joinTitle: "Your Kiwi starts here.",
    joinLines: ["Join Price Chat.", "Participate.", "Earn your rank."],
    joinCta: "Join Price Chat",
    // what a first-time visitor gets, in one line under the call to action (signed-out visitors only)
    joinNote: "Free, no password. Earn Kiwi XP for watching, reading and chatting, and unlock rewards as your Kiwi ranks up.",
  },

  // Our story: where we come from, told forward. No dispute, no claims about anyone: we were part of something, we
  // learned from it, our paths changed, now we're building our own thing. Approved wording, verbatim.
  story: {
    eyebrow: "Our story",
    title: ["From Banter", "to Price Chat."],
    lead: [
      "We spent years helping build and support the Crypto Banter community.",
      "It was an important chapter for us. We met incredible people, built relationships around the world and learned what makes an online community genuinely special.",
    ],
    paragraphs: [
      "Over time, our paths and ideas about what we wanted to build next began to change.",
      "Rather than looking backwards, we decided to take everything we had learned — the experience, relationships, creativity and community spirit — and build something of our own.",
      "Something broader than one market.",
      "A place for people who are curious about crypto, stocks, collectibles, property, cars and everything else that has a price.",
    ],
  },

  footer: {
    links: [
      { label: "About", href: "/#story" },
      { label: "Contact", href: "/contact" },
      { label: "Disclaimer", href: "/disclaimer" },
      { label: "Terms", href: "/terms" },
      { label: "Privacy", href: "/privacy" },
    ],
  },
};

export const NETWORK_LABEL: Record<Network, string> = { telegram: "Telegram", x: "X", youtube: "YouTube" };
