# News Desk feeds: second pass on UNCLEAR sources, compliant replacements, licence routes (2026-11-24)

Follows `feed_terms_audit.md` and uses its classes: PERMITTED / LICENCE NEEDED / NON-COMMERCIAL / UNCLEAR. One class is added here: **CONDITIONAL**. It means the terms allow reuse, but with a condition the owner must accept, or the terms contain a conflict. This is a reading of the published terms, not legal advice. No repository file was edited.

How the checks were done:
- **Feeds:** each feed was fetched with a browser user agent (and a declared contact user agent for sec.gov) by `scratchpad/term/scripts/verify.py`. The script parsed the feed as RSS or Atom and counted the items dated within the last 7 days.
- **robots.txt:** each feed URL was checked against its host's robots.txt for `User-agent: *`. Where the parser and the file disagreed, the file was read by hand.
- **Terms:** pages were read with curl or WebFetch. Where both were blocked, the text was taken from a search-engine snippet of the publisher's own page, and is labelled **(snippet)**.

## Counts

| | |
|---|---|
| UNCLEAR sources re-examined | 14 (13 RSS publishers + FAA) |
| Now NON-COMMERCIAL | 4: CoinDesk, Politico, ABC Australia, Mining.com |
| Now LICENCE NEEDED | 5: FT, CryptoSlate, The Defiant, Bitcoin Magazine, Meduza |
| Now PERMITTED | 1: FAA (federal work, by statute) |
| Still UNCLEAR | 4: Unchained, DW, gCaptain, Moscow Times. Treat as not usable: all rights are reserved by default and no grant was found. |
| New replacement feeds that are live **and** PERMITTED/CONDITIONAL | 33 ready-to-paste entries (section 2). The existing PERMITTED feeds are listed as well. |
| Live but rejected (terms or robots) | 20+ (table 2b) |

---

## 1. Reclassified UNCLEAR sources

| Source (feed) | New class | Evidence (verbatim) | URL |
|---|---|---|---|
| **Financial Times** (2 feeds) | LICENCE NEEDED (FT syndication). The current page could not be read; the evidence is archived. | The feed's `<copyright>` points to the FT terms. The archived FT terms (2012-2015) say RSS restrictions are that you "only publish the feed for access via a web-based browser" and "do not use or publish the feed as part of a paid for service or for other commercial gain". FT provides licences "for organisations that wish to systematically republish our headlines as part of a commercial service" (syndication.ft.com). | help.ft.com is JavaScript-only (Salesforce) to curl and WebFetch refuses it. Archive: https://webarchive.library.unt.edu/web/20150114085915/http://www.ft.com/servicestools/help/terms (snippet) |
| **CoinDesk** | NON-COMMERCIAL | "Except as may be otherwise indicated on the Services, you are only authorized to view, play, print and download documents, audio and video found on our Services for personal, informational, and non-commercial purposes only." | https://www.coindesk.com/terms (snippet; the page and feed return 403 to curl and WebFetch) |
| **CryptoSlate** | LICENCE NEEDED (written permission) | The feeds FAQ says RSS access does not grant republication or syndication rights. For redistribution or commercial use: "contact CryptoSlate to request written permission"; "Keep CryptoSlate attribution and links intact in any permitted use." | https://cryptoslate.com/feeds/ (snippet; 403 direct) |
| **Bitcoin Magazine** | LICENCE NEEDED (prior written consent) | "Unauthorized reproduction, distribution, or modification is prohibited without our prior written consent." No RSS carve-out; re-read today. | https://bitcoinmagazine.com/terms-of-use |
| **The Defiant** | LICENCE NEEDED (sponsors@thedefiant.io) | "5.2 We grant you a personal, non-exclusive, non-transferable, revocable license to access the Services and to share links, headlines, and brief excerpts with attribution and a link back to the original article. That is the whole license: republication of full articles, systematic redistribution, or any commercial reuse of our content requires our prior written permission (sponsors@thedefiant.io)." An automated commercial desk is "systematic redistribution" and "commercial reuse". | https://thedefiant.io/terms-conditions (read directly) |
| **Unchained** | still UNCLEAR | All unchainedcrypto.com pages, feeds and llms.txt return 403 to curl and WebFetch. Searches surface only unrelated "Unchained" companies (unchained.com, a bitcoin lender). | tried /terms/, /terms-of-service/, /terms-of-use/ |
| **Politico** | NON-COMMERCIAL | The terms govern "the www.politico.com website": "You may use the Site, and materials on the Site, for your personal, non-commercial use only"; "You may not otherwise download, republish, retransmit or reproduce any of the materials". Effective date 7/11/2016, read from Politico's own subscriber mirror. | https://subscriber.politicopro.com/terms-of-service (politico.com returns 403 and WebFetch refuses the domain) |
| **ABC Australia** | NON-COMMERCIAL | "ABC Content is provided for your personal, non-commercial use only. Unless specifically noted in the House Rules for a particular service, you may not otherwise reproduce, republish ... ABC Content, without obtaining the ABC's prior written permission." | https://help.abc.net.au/hc/en-us/articles/360001548096-ABC-Terms-of-Use (read today, HTTP 200) |
| **DW** | still UNCLEAR | The Legal Notice (via DW's own graph-api) is only the imprint, with no reuse terms. The old "conditions of participation" URL now serves a generic page. Feed `<copyright>` is "2026 DW". No licence grant found. | https://www.dw.com/graph-api/en/content/article/63500643 |
| **Mining.com** | NON-COMMERCIAL (group terms) | The mining.com footer links only to The Northern Miner group's terms. The group copyright notice says: "The contents of this Website are protected by copyright and may be used only for your personal non-commercial purposes. All other rights are reserved and commercial uses including publication, broadcast or redistribution in any medium are prohibited." | https://www.northernminer.com/copyright/ (linked from mining.com) |
| **gCaptain** | still UNCLEAR | The footer has only Privacy Policy. /terms-of-use/ returns 404. llms.txt has no licence text. | https://gcaptain.com/llms.txt |
| **Meduza** | LICENCE NEEDED (newsroom approval). Items tagged CC BY are the exception. | "You cannot republish our content without prior approval from our newsroom." CC BY applies only to the items Meduza releases under it, and "this license does not apply to photographs". | https://meduza.io/en/pages/about |
| **The Moscow Times** | still UNCLEAR | Only About and Privacy pages exist; /page/terms returns 404. Note: Russia lists TMT as "undesirable", so linking is an offence inside Russia (TMT's own notice). | https://www.themoscowtimes.com/page/we-are-now-undesirable-in-russia-heres-what-you-need-to-know |
| **FAA NAS status** (Trending) | PERMITTED (by statute; cite FAA) | 17 U.S.C. §105: "Copyright protection under this title is not available for any work of the United States Government". FAA ESC: "Information presented in this portfolio is considered public information and may be distributed or copied." The data is factual airport status. The "authorized use only" banner covers system access, not reuse. | https://www.esc.gov/mediasolutions/copyright.asp ; https://www.usa.gov/government-copyright |

Two notes on the existing PERMITTED feeds:
- **UN News robots:** `news.un.org/feed/subscribe/` is not disallowed for `*`, read by hand. Python's robot parser misreads that robots.txt, so flagging it as disallowed would be wrong.
- **BLS feed:** `bls.gov/feed/bls_latest.rss` returned 403 to the check today. BLS blocks many automated clients. Check that it still works in production.

---

## 2. Verified compliant replacements by coverage area

Every row below was fetched today (2026-11-24) and parsed as RSS/Atom with recent items. "7d" is the number of items dated within the last 7 days. Rows marked *(prod)* are already in production and are PERMITTED.

### (a) Crypto: almost nothing compliant
| name | url | 7d / newest | Terms (quote, URL) | Attribution | robots |
|---|---|---|---|---|---|
| CFTC (prod) | https://www.cftc.gov/RSS/RSSGP/rssgp.xml | 2 / 11-23 | public domain (see audit) | acknowledge CFTC | allowed |
| CFTC Enforcement | https://www.cftc.gov/RSS/RSSENF/rssenf.xml | 2 / 11-23 | same | same | allowed |
| SEC (prod) + SEC Statements | https://www.sec.gov/news/statements.rss | 2 / 11-20 | public domain (see audit) | cite SEC | allowed |
| Ethereum Foundation blog | https://blog.ethereum.org/feed.xml | 1 / 11-23 | CONDITIONAL: "all material ... on the Websites ... are licensed under the Creative Commons Attribution 4.0 International License". The same terms also say users warrant they use the Websites "only for your own personal use", which conflicts with the licence. https://ethereum.org/en/terms-of-use/ (covers "any website published by the Foundation") | CC BY: credit + licence link | allowed |
| Bitcoin Optech | https://bitcoinops.org/feed.xml | 1 / 11-20 | "All material produced by Bitcoin Optech is open source and released under the MIT license." https://bitcoinops.org/en/about/ | keep the MIT notice/credit | no robots.txt |

There is no compliant fast crypto news feed. Coverage would need a licence (section 3).

### (b) US/global markets and companies
| name | url | 7d / newest | Terms | Attribution | robots |
|---|---|---|---|---|---|
| SEC EDGAR XBRL filings (all) | https://www.sec.gov/Archives/edgar/xbrlrss.all.xml | 200 / 11-24 | SEC public information (audit) | cite SEC | allowed |
| SEC EDGAR latest 8-K (Atom) | https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&start=0&count=40&output=atom | 40 / 11-24 | same. SEC documents this as an official feed ("Latest Filings ... subscribe to all filings, or filter by ... Form Type", https://www.sec.gov/about/rss-feeds) | cite SEC; send a declared User-Agent with contact | **robots disallows /cgi-bin**. Use the Archives feed or data.sec.gov instead if robots is a hard rule. |
| Nasdaq Trade Halts | https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts | 46 / 11-24 | CONDITIONAL: the feed terms contain no non-commercial or no-redistribution clause, but "Subscribers are prohibited from editing, modifying, misrepresenting ... the Feed, except as may be set forth by separate agreement." Poll at most once a minute. https://www.nasdaqtrader.com/content/administrationsupport/agreementstrading/THRSSFeedTermsCond.pdf | name Nasdaq; restate halts factually, do not alter codes | allowed |
| Federal Reserve (prod) | https://www.federalreserve.gov/feeds/press_all.xml | 4 / 11-20 | public domain (audit) | cite the Board | none |
| OCC news | https://www.occ.gov/rss/occ_news.xml | 1 / 11-18 | US federal work (17 U.S.C. §105) | cite OCC | allowed |

The press-release wires were checked and are **not** usable as-is:
- **PR Newswire:** feeds are live, but the general terms allow "a single copy ... solely for your personal, noncommercial use" (https://www.prnewswire.com/terms-of-use/). Web publishers are sent to a separate programme ("Add PR Newswire content to your Web site", https://www.prnewswire.com/rss/).
- **GlobeNewswire:** live; terms could not be read (403).
- **Business Wire:** live, but robots is unreadable and the terms page returns 403.

Treat all three as LICENCE NEEDED through their media-partner or publisher feed programmes. These are probably free, but that is an estimate.

### (c) World / geopolitics / breaking
| name | url | 7d / newest | Terms | Attribution | robots |
|---|---|---|---|---|---|
| White House (prod) + Presidential Actions | https://www.whitehouse.gov/presidential-actions/feed/ | 3 / 11-23 | not copyright protected (audit) | none required | allowed |
| UN News (prod) | https://news.un.org/feed/subscribe/en/news/all/rss.xml | 30 / 11-24 | CONDITIONAL (audit): "News-related material can be used as long as the appropriate credit is given and the United Nations is advised." | credit + notify the UN | allowed (manual read) |
| UN Press (meetings coverage) | https://press.un.org/en/rss.xml | 10 / 11-24 | same UN carve-out, https://www.un.org/en/about-us/copyright | same | allowed |
| US Dept of Defense releases | https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=10 | 8 / 11-20 | "Information presented on this website is considered public information and may be distributed or copied unless otherwise specified." https://defense.gov/Legal-Administrative/Privacy-Security (snippet; 403 direct) | cite DoD; no seals | allowed |
| US DoD news | https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=10 | 10 / 11-23 | same | same | allowed |
| US Dept of Justice | https://www.justice.gov/news/rss?type=press_release&m=1 | 25 / 11-23 | "Unless otherwise indicated, information on Department of Justice websites is in the public domain and may be copied and distributed without permission." https://www.justice.gov/legalpolicies | no seals or logos | allowed |
| GOV.UK all news | https://www.gov.uk/search/news-and-communications.atom | 20 / 11-24 | OGL v3: "You are free to: copy, publish, distribute and transmit the Information ... exploit the Information commercially and non-commercially" https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/ ; GOV.UK: "You can reproduce content published on GOV.UK under the OGL" https://www.gov.uk/help/terms-conditions | "Contains public sector information licensed under the Open Government Licence v3.0." | allowed |
| UK Prime Minister's Office | https://www.gov.uk/search/news-and-communications.atom?content_purpose_supergroup%5B%5D=news_and_communications&organisations%5B%5D=prime-ministers-office-10-downing-street | 9 / 11-24 | OGL v3 | OGL statement | allowed |
| UK Ministry of Defence | https://www.gov.uk/government/organisations/ministry-of-defence.atom | 20 / 11-24 | OGL v3 | OGL statement | allowed |
| UK FCDO | https://www.gov.uk/government/organisations/foreign-commonwealth-development-office.atom | 20 / 11-24 | OGL v3 | OGL statement | allowed |
| European Commission press corner | https://ec.europa.eu/commission/presscorner/api/rss?language=en | 10 / 11-24 | "content owned by the EU on this website is licensed under the Creative Commons Attribution 4.0 International (CC BY 4.0) licence ... reuse is allowed, provided appropriate credit is given and changes are indicated." https://commission.europa.eu/legal-notice_en | "© European Union" + note any changes | allowed |
| Council of the EU press | https://www.consilium.europa.eu/en/rss/pressreleases.ashx | 20 / 11-24 | CONDITIONAL: reproduction authorised if the source is credited, the meaning is kept and changes are flagged. Commercial use is not addressed. https://www.consilium.europa.eu/en/about-site/copyright/ (snippet; 403 direct) | "© European Union, 2026" | robots 403 (unknown) |
| USGS significant earthquakes | https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.atom | 8 / 11-24 (M6.4 Mauritius) | "Public Domain" (USGS-authored); "not all information, illustrations, or photographs on our site are" https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits | credit USGS | no robots.txt |
| GDACS disasters (EU JRC/UN) | https://www.gdacs.org/xml/rss.xml | 130 / 11-24 | GDACS documents apply the Commission reuse policy (Decision 2011/833/EU): CC BY 4.0 unless otherwise noted. API guide asks for the credit "Global Disaster Alert and Coordination System, GDACS". Terms PDF: https://www.gdacs.org/documents/2025/GDACS_Terms_of_use_Mar_25.pdf (snippet; the page is behind a JavaScript challenge) | credit GDACS | allowed |
| NOAA National Hurricane Center (Atlantic) | https://www.nhc.noaa.gov/index-at.xml | 2 / 11-25 | "The information on National Weather Service (NWS) Web pages are in the public domain, unless specifically noted otherwise, and may be used without charge for any lawful purpose" (don't claim it as your own, don't imply endorsement) https://www.weather.gov/disclaimer | credit NOAA/NWS | allowed |
| NASA news releases | https://www.nasa.gov/news-release/feed/ | 10 / 11-24 | "News outlets ... may use NASA content without needing explicit permission"; "NASA content used in a factual manner that does not imply endorsement may be used" https://www.nasa.gov/nasa-brand-center/images-and-media/ | no NASA insignia or endorsement | allowed |
| Global Voices | https://globalvoices.org/feed/ | 13 / 11-24 | CC BY: "copy and redistribute ... for any purpose, even commercially" with attribution https://globalvoices.org/about/global-voices-attribution-policy/ | credit + link + licence | allowed |
| The Conversation (global) | https://theconversation.com/global/articles.atom | 12 / 11-23 | CONDITIONAL: "you can republish our articles online or in print for free". "Extracts: you can run the first few lines ... 'Read the full article on The Conversation' with a link". "Commercial, non-journalism usage: license fees may apply." https://theconversation.com/us/republishing-guidelines | credit + link; no edits | allowed |

### (d) Energy / commodities
| name | url | 7d / newest | Terms | Attribution | robots |
|---|---|---|---|---|---|
| US Dept of Energy newsroom | https://www.energy.gov/rss/newsroom.xml | 4 / 11-20 | US federal work (17 U.S.C. §105) | cite DOE | allowed |
| UK DESNZ | https://www.gov.uk/government/organisations/department-for-energy-security-and-net-zero.atom | 20 / 11-24 | OGL v3 | OGL statement | allowed |
| Ofgem | https://www.ofgem.gov.uk/rss.xml | 3 / 11-24 | Ofgem is a UK public body; OGL is likely but its page was not read (EST) | — | allowed |
| WTO news (trade/tariffs) | https://www.wto.org/library/rss/latest_news_e.xml | 8 / 11-24 | UNCLEAR-leaning-licence: archived WTO terms allow reproduction for non-commercial purposes; commercial use needs permission from publications@wto.org (snippet, archived). **Not ready.** | — | allowed |

Feeds that are live and public domain but **robots-blocked**:
- **EIA:** `Disallow: /rss` blocks https://www.eia.gov/rss/todayinenergy.xml and press_rss.xml. EIA's own API (api.eia.gov, free key) is the compliant route for inventory/STEO data.

Not readable today:
- **IEA:** /api/rss/news returns 404 and the site returns 403.
- **OPEC:** 403.

### (e) Central banks / macro data
| name | url | 7d / newest | Terms | Attribution | robots |
|---|---|---|---|---|---|
| Fed FOMC/monetary | https://www.federalreserve.gov/feeds/press_monetary.xml | 1 / 11-18 | public domain (audit) | cite Board | none |
| Fed speeches | https://www.federalreserve.gov/feeds/speeches.xml | 4 / 11-20 | same | same | none |
| ECB (prod) | https://www.ecb.europa.eu/rss/press.html | 7 / 11-24 | audit | cite ECB, accurate | allowed |
| Bank of Canada press | https://www.bankofcanada.ca/content_type/press-releases/feed/ | 0 / 11-16 | "the Bank permits you to freely use, copy, distribute and transmit its website content". Conditions: attribute the Bank, be accurate, and tell buyers if the content is used in "paid services". https://www.bankofcanada.ca/terms/ | "Source: Bank of Canada"; Telegram/X are free, so the notice duty does not apply | allowed |
| RBA media releases | https://www.rba.gov.au/rss/rss-cb-media-releases.xml | 0 / 11-06 | "all RBA material, apart from excluded material, is provided under a CC BY 4.0 licence" https://www.rba.gov.au/copyright/ (snippet; 403 direct; other RBA feeds return 403 to us) | CC BY | allowed |
| RBNZ news | https://www.rbnz.govt.nz/feeds/news | 2 / 11-24 | CONDITIONAL: reuse permitted "without specific permission" if the Bank is credited and material is reproduced accurately and not misleadingly. https://www.rbnz.govt.nz/copyright (snippet; 403 direct) | credit RBNZ | allowed |
| BIS press releases | https://www.bis.org/doclist/all_pressrels.rss | 1 / 11-18 | CONDITIONAL: "Users may reproduce a limited extract of BIS Material ... in other publications or products free of charge, provided the BIS is cited as the source." Whole-item redistribution is non-commercial only. https://www.bis.org/terms_conditions.htm | cite BIS; headline + short extract only | allowed |
| US Census economic indicators | https://www.census.gov/economic-indicators/indicator.xml | 2 / 11-19 | US federal work (17 U.S.C. §105); Census asks for citation https://www.census.gov/about/policies/open-gov/open-data.html | cite Census | allowed |
| UK ONS release calendar | https://www.ons.gov.uk/releasecalendar?rss | 10 / 11-24 | OGL v3 | OGL statement | no robots.txt |
| UK HM Treasury | https://www.gov.uk/government/organisations/hm-treasury.atom | 16 / 11-24 | OGL v3 | OGL statement | allowed |
| Eurostat (dataset updates) | https://ec.europa.eu/eurostat/api/dissemination/catalogue/rss/en/statistics-update.rss | 949 / 11-23 | CC BY 4.0 (Commission policy) | credit Eurostat | allowed. Too noisy as a news feed; use only as a release trigger. |

Central-bank feeds that are live but fail the terms:
- **Bank of England:** NON-COMMERCIAL (audit).
- **Bank of Japan:** LICENCE NEEDED; it allows copying except "for commercial purposes" (https://www.boj.or.jp/en/about/copyright.htm).
- **SNB:** NON-COMMERCIAL; information may be used "for non-commercial purposes" (https://www.snb.ch/en/srv/disclaimer_copyright).
- **Bundesbank:** CONDITIONAL/unclear. It allows reproduction "for your personal or professional use" citing "Source: Deutsche Bundesbank", but also says "Any further use for commercial purposes, in particular for advertising, is not permitted." (https://www.bundesbank.de/en/homepage/user-information/conditions-for-the-general-use-of-the-website-764706). Not recommended.

Feeds that are robots-blocked:
- **BEA:** https://apps.bea.gov/rss/rss.xml is public domain but blocked by `Disallow: /`.
- **TreasuryDirect auctions:** https://www.treasurydirect.gov/TA_WS/securities/announced/rss is blocked by `Disallow: /`.
- **FDIC via GovDelivery:** public.govdelivery.com has `Disallow: /`.

### (f) Official / regulators / exchanges / IR
Everything in (b), (c) and (e) belongs here too. The notes below cover what is left:
- **ESMA:** the feed https://www.esma.europa.eu/rss.xml is live but has no item dates, and its legal page has no reuse clause. EU-agency CC BY is likely but was not verified (EST).
- **Exchange and crypto company newsrooms:** Coinbase, Kraken, Binance, CME, OPEC and IMF were all blocked or had no RSS. Tether's terms: "You must not use any part of the content on our Site for commercial purposes without obtaining a license" (https://tether.io/terms/), so LICENCE NEEDED.
- **Chainwire (crypto press-release wire):** the feed is live, but its terms grant no content licence, so UNCLEAR.
- **Treasury/OFAC:** OFAC retired its RSS feed on 2025-01-31 (OFAC notice; it now uses email via GovDelivery). No Treasury press-release RSS was found.
- **State Dept:** state.gov returned HTTP 407 from this network. The terms would be public domain, but the feed could not be verified.

### Ready to paste

These are the new feeds rated PERMITTED or low-risk CONDITIONAL. Already-in-production feeds and robots-blocked feeds are left out.

```python
COMPLIANT_FEEDS = [
    # (a)/(f) regulators touching crypto
    {"name": "CFTC Enforcement", "url": "https://www.cftc.gov/RSS/RSSENF/rssenf.xml", "tier": 0, "category": "crypto"},
    {"name": "SEC Statements", "url": "https://www.sec.gov/news/statements.rss", "tier": 0, "category": None},
    {"name": "Bitcoin Optech", "url": "https://bitcoinops.org/feed.xml", "tier": 2, "category": "crypto"},
    # (b) markets & companies (primary)
    {"name": "SEC EDGAR filings", "url": "https://www.sec.gov/Archives/edgar/xbrlrss.all.xml", "tier": 1, "category": "stocks"},
    {"name": "Nasdaq Trade Halts", "url": "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts", "tier": 1, "category": "stocks"},
    {"name": "OCC", "url": "https://www.occ.gov/rss/occ_news.xml", "tier": 0, "category": "economics"},
    # (c) world / geopolitics / disasters
    {"name": "White House Actions", "url": "https://www.whitehouse.gov/presidential-actions/feed/", "tier": 0, "category": "politics"},
    {"name": "UN Press", "url": "https://press.un.org/en/rss.xml", "tier": 0, "category": "politics"},
    {"name": "US Defense Releases", "url": "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=10", "tier": 0, "category": "politics"},
    {"name": "US Defense News", "url": "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=10", "tier": 0, "category": "politics"},
    {"name": "US Justice Dept", "url": "https://www.justice.gov/news/rss?type=press_release&m=1", "tier": 0, "category": "politics"},
    {"name": "GOV.UK News", "url": "https://www.gov.uk/search/news-and-communications.atom", "tier": 0, "category": "politics"},
    {"name": "UK Prime Minister", "url": "https://www.gov.uk/search/news-and-communications.atom?content_purpose_supergroup%5B%5D=news_and_communications&organisations%5B%5D=prime-ministers-office-10-downing-street", "tier": 0, "category": "politics"},
    {"name": "UK Ministry of Defence", "url": "https://www.gov.uk/government/organisations/ministry-of-defence.atom", "tier": 0, "category": "politics"},
    {"name": "UK FCDO", "url": "https://www.gov.uk/government/organisations/foreign-commonwealth-development-office.atom", "tier": 0, "category": "politics"},
    {"name": "European Commission", "url": "https://ec.europa.eu/commission/presscorner/api/rss?language=en", "tier": 0, "category": "politics"},
    {"name": "USGS Earthquakes", "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.atom", "tier": 0, "category": None},
    {"name": "GDACS", "url": "https://www.gdacs.org/xml/rss.xml", "tier": 1, "category": None},
    {"name": "NOAA Hurricane Center", "url": "https://www.nhc.noaa.gov/index-at.xml", "tier": 0, "category": None},
    {"name": "NASA", "url": "https://www.nasa.gov/news-release/feed/", "tier": 1, "category": None},
    {"name": "Global Voices", "url": "https://globalvoices.org/feed/", "tier": 2, "category": "politics"},
    {"name": "The Conversation", "url": "https://theconversation.com/global/articles.atom", "tier": 2, "category": None},
    # (d) energy
    {"name": "US Energy Dept", "url": "https://www.energy.gov/rss/newsroom.xml", "tier": 0, "category": "commodities"},
    {"name": "UK Energy Dept", "url": "https://www.gov.uk/government/organisations/department-for-energy-security-and-net-zero.atom", "tier": 0, "category": "commodities"},
    # (e) central banks / macro
    {"name": "Fed FOMC", "url": "https://www.federalreserve.gov/feeds/press_monetary.xml", "tier": 0, "category": "economics"},
    {"name": "Fed Speeches", "url": "https://www.federalreserve.gov/feeds/speeches.xml", "tier": 0, "category": "economics"},
    {"name": "Bank of Canada", "url": "https://www.bankofcanada.ca/content_type/press-releases/feed/", "tier": 0, "category": "economics"},
    {"name": "RBA", "url": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml", "tier": 0, "category": "economics"},
    {"name": "RBNZ", "url": "https://www.rbnz.govt.nz/feeds/news", "tier": 0, "category": "economics"},
    {"name": "BIS", "url": "https://www.bis.org/doclist/all_pressrels.rss", "tier": 0, "category": "economics"},
    {"name": "US Census Indicators", "url": "https://www.census.gov/economic-indicators/indicator.xml", "tier": 0, "category": "economics"},
    {"name": "UK ONS", "url": "https://www.ons.gov.uk/releasecalendar?rss", "tier": 0, "category": "economics"},
    {"name": "HM Treasury", "url": "https://www.gov.uk/government/organisations/hm-treasury.atom", "tier": 0, "category": "economics"},
]
```

Conditions the desk must apply to these feeds:
- **UN:** credit the source and **advise the UN** of the use.
- **CC BY feeds** (EC, GDACS, RBA, Global Voices, Ethereum): credit, link to the licence, and indicate changes.
- **OGL feeds:** add the OGL attribution statement, for example in the channel bio or a pinned post.
- **BIS:** short extract only.
- **Nasdaq halts:** do not alter or misrepresent the halts.
- **SEC:** send a declared User-Agent.
- **Conversation:** extract + "Read the full article" link.
- **All government feeds:** no seals or logos, and nothing that implies endorsement.

Tier and category values follow the existing conventions in `sources.py` (official sources are tier 0). They are suggestions only.

---

## 3. Licence options

Researched by a helper agent today. Text in quotes is **QUOTED FACT** from the URL given; **EST** marks a judgement.

| Provider | What it permits for public social posting | Public pricing | Fit |
|---|---|---|---|
| **AP** Media API | Rights only by contract ("Access all your licensed content in one place… 30 days of content", https://developer.ap.org/ap-media-api/) | "Contact us" | EST: the best fast world wire. The contract must name X/Telegram. EST: five figures a year or more. |
| **Reuters** (Reuters Connect / LSEG) | No public terms; B2B ("Contact us to arrange a demo and discuss pricing, entitlements…", lseg.com snippet) | Contact sales | EST: same position as AP |
| **AFP** API | No published licence; free trial at developers.afp.com (trade press) | Contact sales | EST: strong in the Middle East, Europe and Africa |
| **Dow Jones** Newswires / Factiva | Standard licence: "limited, personal, non-transferable… does not permit any downloading or redistribution" (cision.com DJ appendix, snippet) | Contact sales (djreprints) | Not usable without a bespoke redistribution deal |
| **Benzinga** direct | "email us directly at licensing@benzinga.com" (https://docs.benzinga.com/introduction/introduction). The basic tier delivers "headline, body teaser, and a hyperlink to read the full story on Benzinga.com" (AWS listing, snippet). | Contact sales | EST: the most realistic licensed US markets/company feed for a small product. Ask for a social display licence. |
| **Massive (ex-Polygon)** Benzinga News | Individual plans are "Individual use only" (https://massive.com/partners/benzinga). Display in your own app needs a Business plan. | "$99/month" individual (not usable); Business: sales@massive.com | Business tier only |
| **NewsAPI.ai / Event Registry** | Own terms allow you to "Show your users headlines, short excerpts, the source name, the publication date, a thumbnail image", but "Our permission covers our side only" (https://newsapi.ai/terms) | "5K plan… $90 / month", $150, $400, $3,000 (https://newsapi.ai/plans) | Aggregator. Publisher copyright still applies to the headline and snippet, so it is a discovery tool, not a licence. |
| **NewsAPI.org** | You may not "use the service to reproduce or republish copyrighted material" (https://newsapi.org/terms) | Business "$449/month… For production and published commercial projects" (https://newsapi.org/pricing) | Aggregator; no republication rights |
| **GDELT** | "unlimited and unrestricted use for any academic, commercial, or governmental use"; cite and link GDELT (https://www.gdeltproject.org/about.html) | Free | Covers GDELT's data only, not the indexed headlines. Use as a detection signal. |
| **CryptoPanic** API | Pages render only with JavaScript. Snippets show "CryptoPanic attribution" on some tiers, plus an Enterprise API. | Not readable; Enterprise is contact sales | Aggregator of third-party outlets: discovery only |
| **Alpha Vantage** news | "personal, non-commercial purposes" unless agreed in writing (ToS snippet) | Contact sales | Not usable |
| **Finnhub** news | "strictly for personal use unless explicitly stated otherwise"; no redistribution (https://finnhub.io/terms-of-service) | Contact sales | Not usable |
| **Marketaux** | "solely for your personal, non-commercial use" (https://www.marketaux.com/tos) | $29–$199/mo (https://www.marketaux.com/pricing) | Not usable as written |
| **Tiingo** news | Internal tiers "may not display or share the data" (https://www.tiingo.com/about/pricing) | $30/$50 internal; redistribution tier separate | Redistribution tier only |
| **Messari** (Blockworks) | "If you are interested in redistributing any data from our API, please reach out" (docs.messari.io, snippet) | Enterprise | Crypto option; needs a deal |
| **CoinDesk Data / The Block Pro** | API licence (data.coindesk.com/api-licence-agreement) has no display clause; The Block Pro APIs are licence-gated | Contact sales | Ask about news display rights |
| **Publisher direct** (from the audit) | NYT (nytlicensing.com), BBC (metadata licence), Le Monde (syndication@lemonde.fr), FT (syndication.ft.com), CryptoSlate / The Defiant / Bitcoin Magazine (written permission), PR Newswire / Business Wire / GlobeNewswire (publisher feed programmes) | Contact | EST: the crypto outlets and wires may grant headline+link permission cheaply or free; worth emailing |

---

## 4. Coverage gaps if only compliant feeds are kept

1. **Crypto news: the gap is close to total.** The compliant feeds are regulators (SEC, CFTC, OCC), the Ethereum blog and Bitcoin Optech. None of the crypto trade press is compliant: CoinDesk, Cointelegraph, Decrypt and The Block are NON-COMMERCIAL; CryptoSlate, The Defiant and Bitcoin Magazine need a licence; Unchained is UNCLEAR. That leaves no source for exchange hacks, ETF flows, token listings, or the Binance and Coinbase announcements. Routes:
   - (i) written permission from CryptoSlate, The Defiant and Bitcoin Magazine, which offer explicit contact routes;
   - (ii) a licence from Benzinga, Messari or CoinDesk;
   - (iii) on-chain and exchange data the desk computes itself.
2. **Fast world breaking news (wars, attacks, coups, elections abroad).** Government feeds give official statements, often hours late, and only for the US, UK and EU. USGS, GDACS and NHC cover natural disasters well. There is no compliant wire for conflict or security events in the Middle East, Asia or Africa: Al Jazeera, BBC, France 24, CNA, DW and Politico are all out. This is the gap only AP, Reuters or AFP closes. GDELT can serve as a free detection signal, with posts written from official confirmations.
3. **US markets and company news.**
   - EDGAR filings and Nasdaq halts give primary facts: earnings 8-Ks and halts.
   - There is no compliant market wrap or "why stocks moved" source: CNBC, MarketWatch, Bloomberg, FT and Fox Business are all out.
   - The press-release wires need a publisher-programme agreement.
   - Benzinga is the practical licence.
4. **Energy and commodities.** OilPrice, Mining.com and gCaptain are out. EIA RSS is robots-blocked (use the EIA API instead). OPEC and IEA could not be read. Only DOE and UK DESNZ remain, which means nothing on oil-market moves or shipping incidents. EST: shipping and Red Sea incidents could come partly from UKMTO or MARAD advisories, which were not checked.
5. **Macro data.** Coverage is good: Fed, ECB, BLS, Census, ONS, Bank of Canada, RBA, RBNZ and BIS.
   - BEA and TreasuryDirect are robots-blocked; BEA has an API.
   - Bank of England is NON-COMMERCIAL, and BoJ and SNB fail the commercial test.
   - The Japan and Switzerland policy decisions would therefore have to be reported from other compliant sources or licensed.
6. **Low-volume caveats.** Bank of Canada, RBA, BIS and Riksbank posted 0–1 items in the last 7 days. That is normal for central banks, so don't drop them for being quiet.

### Rejected or blocked candidates (for the record)

| Candidate | Reason |
|---|---|
| EIA RSS, BEA RSS, TreasuryDirect RSS, FDIC (GovDelivery), NOAA SPC, api.weather.gov | robots.txt disallows the path |
| PR Newswire, GlobeNewswire, Business Wire, Chainwire | no public reuse grant; publisher programme or licence needed |
| BoE, BoJ, SNB, Bundesbank, WTO, Tether | non-commercial, licence needed, or a conflicting commercial clause |
| VOA | feed stale (newest item 2025-03-15) |
| RFE/RL | robots disallows /api/*, and the feed was empty |
| WHO, FBI | feeds stale (newest 10-10 and 11-10) |
| IMF, OPEC, IEA, CISA, DHS, DOL, USDA, FCA, FERC, NRC, OECD, Coinbase, Kraken, Binance, CME, State Dept | HTTP 403, 404 or 407, or no RSS found; not verified |
