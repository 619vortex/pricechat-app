# Major incidents: why they were missed, what changed (2026-11-17)

Owner, 2026-11-17: Price Chat missed the US banking outages, the Microsoft outage and airline groundings. This is the audit and the fix.

## What happened (verified)

| Incident | When (UTC) | Facts | Connected? |
|---|---|---|---|
| Microsoft 365 outage (Outlook, Teams, SharePoint, Copilot) | 2026-11-12 from ~16:17; acknowledged 17:54; restored by 11-14 06:42 | Network capacity upgrade + network change; 74+ countries; no malicious activity per Microsoft | No |
| FAA nationwide ground stop | 2026-11-13, 4+ hours, lifted by 21:28 | "Instability in our radar and our communications" (Transportation Secretary) | Microsoft: "no indication" it was related. Not connected |
| JPMorgan Chase outage | 2026-11-16, Downdetector reports from 14:53, spike ~18:00, ~8 h | Software bug in on-premises hardware, not cyber (Chase). Bank of America and Wells Fargo reports, causes not stated. U.S. Bank: "not directly impacted", Zelle delays with other banks | Not assumed. Chase said other banks may have been affected as transaction counterparties, "not a systemic issue" |
| United Airlines system outage | 2026-11-16 ~22:10, resolved by ~23:41 | "Widespread system outage", flights delayed | No |

## Why each was missed (production logs and the desk's own feeds)

1. **Banks (11-16): never discovered.** `newsdesk.engine.collect` stores a headline only if it has a market keyword. "Outage",
   "bank" and "banking" were not keywords, so "JPMorgan Chase systems recovering following hourslong nationwide technical outage" (Fox
   Business 18:51), "JPMorgan Chase says temporary software bug is affecting consumer banking" (CNBC 22:14), BBC 00:18 and CBS 01:09 were
   discarded before storage, and Trending (which reads the desk's stored stories) never saw them. The one telling stored ("Financial
   stocks slide as online banking outages...", Fox Business 20:06, because of "stocks") had one outlet and never reached the top five.
   Trending's own classifier also missed the plural "outages". Production's top five that afternoon carried Google's "chase" trend
   (10K+) on "Armed carjacking leads to police chase in Royal Oak".
2. **United (11-16): found, then capped.** First seen 22:39 at 61.7, held "No reliable source backs this topic yet" (Google's linked
   outlets were not on the list), 78.5 at 23:00, then "Trending's daily limit reached (3/3 in 24 h)" (posts 152, 167, 204) until it went
   stale at 03:04.
3. **FAA (11-13) and Microsoft (11-12): never discovered.** Dropped by the same collect rule. Trending Topics went live 11-14 13:33.

## What changed

- `app/newsdesk/incidents.py`: one deterministic detector for banking, payments, exchanges, aviation, cloud/email, telecom, power and
  cyber incidents. Major = the event itself, told plainly, at a named major company or at national scale. Features, explainers,
  retrospectives ("years of warnings before..."), hedged reports ("reportedly") and reassurance are not major. `same_incident` joins two
  headlines only for the same kind at the same company (or two sector-wide reports): Chase is not "major US banks", Microsoft is not the
  FAA, United is not the FAA.
- News Desk: incident headlines are stored (financial -> `stocks`, others -> `viral`), logged as `newsdesk: incident story stored:`.
  A major incident is important news (significance 30, consequence 12 for financial or systemic providers); verified by two outlets it
  scores 72 fresh and may break (bar 70). One outlet never breaks.
- Trending: incidents are on the beat (`markets` or `technology`), get incident impact and surprise, two credible outlets within the
  hour count as a burst, a rising Google search for the affected service counts as interest. The 75 line is unchanged.
- Evidence: automatic only when verified: two independent outlets, the affected company's own statement, or an official source.
  More national outlets count as reliable (USA Today, Newsweek, The Independent, Fox Business, ...); a list of outage reporters
  (Yahoo, Tom's Guide, local TV) counts only for a headline that reads as a major incident.
- FAA airport status (official, free, every 5 minutes): ground stops at 5+ airports, or 2+ for an equipment or system failure.
- Google Trends every 5 minutes (was 10).
- Limits: a verified major incident may go out beyond Trending's 3-a-day limit, at most 2 more in 24 hours, 60 minutes after the last
  trend post. The shared X cap (20), the News Desk's spacing, the stops, dedup and every gate still apply.
- Admin › X › Trending Topics › **Major incidents**: each incident's event time, first detection, score and peak, evidence, and the
  outcome with its reason; plus the audited misses above. Logs: `trending incident:` once per status change.

## How fast now (replays in tests/test_incidents_20261116.py)

| Incident | Before | Now |
|---|---|---|
| Banks | never | topic 18:56 (5 min after Fox Business), unverified; automatic at 01:14 from feeds alone, or at the first scan of Google's "chase" trend (scores 88) |
| United | never (expired 03:04) | over 75 and verified at the first scan (22:39); sent by the incident allowance |
| FAA ground stop | never | within one 5-minute scan of the FAA listing the ground stops |
| Microsoft | never | stored and classified as soon as a feed or Google's linked outlets carry it |

What is still not covered: Microsoft 365's own status (posted on X, a paid read, not enabled), Downdetector (no free API), X trends (paid).
