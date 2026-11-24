# Feed permission requests: drafts for the owner (2026-11-24)

These are drafts only: nothing has been sent and nothing has been bought. The owner sends each request from Price Chat's own address and
fills in the placeholders in [brackets]. No feed's use is expanded while the requests are open.

## Who to ask first

Ranked by what the desk loses without them. Usage counts the 7-day replay's Telegram posts that credited the outlet (34 posts in all). The
class comes from `01-terms-audit.md` and `02-replacements-and-licences.md`.

| # | Publisher | Class today | Why it matters | Contact route | Ask |
|---|---|---|---|---|---|
| 1 | CoinDesk | non-commercial | Core crypto news; no compliant equivalent | Not verified: use CoinDesk's licensing or partnerships page | Template A |
| 2 | Cointelegraph | non-commercial | Core crypto news | Not verified: use the site's contact page | Template A |
| 3 | The Block | non-commercial | Crypto markets and policy | Not verified: use the site's contact page | Template A |
| 4 | Decrypt | non-commercial | Crypto news | Not verified: use the site's contact page | Template A |
| 5 | CryptoSlate | licence needed (written permission) | Its feeds FAQ sends commercial users to CryptoSlate for written permission | CryptoSlate's contact form (named in its feeds FAQ) | Template A |
| 6 | The Defiant | licence needed | Its terms allow links, headlines and brief excerpts for personal use only | sponsors@thedefiant.io (named in its terms) | Template A |
| 7 | Bitcoin Magazine | licence needed (prior written consent) | Bitcoin news | Not verified: use the site's contact page | Template A |
| 8 | gCaptain | unclear (no terms page) | Shipping and Hormuz: today's 18:26 breaking post (Telegram 213, X 152) credited it | Not verified: use the site's contact page | Template B |
| 9 | The Moscow Times | unclear (no terms page) | Russia/Ukraine: today's 16:00 slot (Telegram 212, X 151) credited it | Not verified: use the site's contact page | Template B |
| 10 | The Guardian | non-commercial (RSS) | World news; the Guardian runs a commercial Open Platform licence | Guardian Open Platform (commercial tier) | Template C |
| 11 | BBC | licence needed (metadata licence; there may be a fee) | World breaking news | BBC metadata licence application (named in its RSS terms) | Template C |
| 12 | Benzinga | licence needed | Market wraps (no compliant source) | licensing@benzinga.com | Template C |
| 13 | AP or Reuters | licence needed | Fast world and conflict news | Their licensing sales pages | Template C (a quote only) |

Templates A and B ask for free written permission for headline-plus-link use. Template C asks for terms and a price only. Every reply
goes to the owner. Nothing is bought, and no feed is added, without the owner's decision.

## Template A: headline-and-link permission (crypto publishers)

Subject: Permission to share [Publisher] headlines with links on Price Chat News

Hello [Publisher] team,

I run Price Chat News, an automated markets news service that posts on X (@thepricechat) and Telegram (@Pricechatupdates). We
are a commercial service, and we want our use of your reporting to be explicitly permitted.

What we would like to do with your RSS feed:
- read it to learn about new stories;
- post a story's headline, or our own one-line summary of it, with a direct link to your article, credited by name ("Source: [Publisher]");
- post at most [3] items from [Publisher] a day, and usually fewer.

What we would not do: republish article text beyond the headline and one sentence, use your images, rehost your content, or get around
a paywall.

Could you confirm in writing that this use is permitted? If it needs a licence or a fee, please tell us the terms and we will
consider them. We are not committing to a purchase in this email.

If you have attribution requirements (wording, logo, or a link format), we will follow them.

Thank you,
[Name]
Price Chat, [website]
[Price Chat contact address]

## Template B: terms clarification (publishers with no published terms)

Subject: Reuse terms for [Publisher]'s RSS feed: headlines and links

Hello [Publisher] team,

Price Chat News is a commercial, automated markets news service on X (@thepricechat) and Telegram (@Pricechatupdates). We
sometimes credit and link to your reporting. For example, on [date] we linked your story "[headline]".

We could not find terms of use for your RSS feed on your site. Could you tell us whether we may:
- read your RSS feed to learn about new stories; and
- post a story's headline, or our own one-line summary of it, with a direct link to your article and a credit ("Source: [Publisher]"),
  at most [3] times a day?

We never use your images, republish full text, or get around a paywall. If you charge for this use, please send the terms. We are not
committing to a purchase in this email.

Thank you,
[Name]
Price Chat, [website]
[Price Chat contact address]

## Template C: licence terms and a quote (licensing desks)

Subject: Licence enquiry: headlines and links for an automated news service on X and Telegram

Hello,

Price Chat News is a commercial, automated markets news service on X (@thepricechat) and Telegram (@Pricechatupdates). It posts about
[6-12] items a day.

We are asking about a licence to:
- receive your headlines (by RSS or API) for story discovery; and
- post a headline, or our own one-line summary of it, with a link to your article and a credit, about [N] items a month from your service.

We would not use full text, images or video.

Could you send your terms and pricing for this use, and say whether a smaller tier exists for headline-and-link use only? This is an
enquiry: we are not committing to a purchase.

Thank you,
[Name]
Price Chat, [website]
[Price Chat contact address]

## Before sending

- Check each contact route on the publisher's own site on the day you send. Only The Defiant's, Benzinga's and Le Monde's addresses
  were read in their terms. The others are not verified.
- Put the real daily maximum in [3]: the desk's per-outlet cap is not fixed, and 3 a day is a conservative ceiling.
- Record each reply in `01-terms-audit.md` and switch the publisher's class in `backend/app/newsdesk/sources.py` (`FEED_LICENCE`)
  only once you have written permission.
