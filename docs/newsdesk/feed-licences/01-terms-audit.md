# News Desk feed terms audit (2026-11-24)

Scope: the 59 production feed entries in `feeds_in_prod.json` (46 publishers), plus the three non-RSS Trending sources (Google Trends, Reddit, FAA). That makes 49 sources in total. Each class is based on what the publisher's own terms say, read on 2026-11-24. This is a report of what the terms say, not legal advice.

How the classes were applied:
- **PERMITTED**: the terms allow reuse with no commercial restriction (government or public-domain sources), or they grant a general reuse right. Any attribution condition is noted.
- **NON-COMMERCIAL**: the free grant covers personal or non-commercial use only. This applies even where the terms say "without our written consent", if they give no licensing route.
- **LICENCE NEEDED**: the terms deal with commercial or business use directly and say it needs a licence, permission, a fee or a separate agreement. Where the terms name the route, it is given.
- **UNCLEAR**: the terms page could not be read (most often a bot block, HTTP 403 or 503), or the terms that were read say nothing about RSS or commercial use.

As of today, none of the NON-COMMERCIAL or LICENCE NEEDED sources can be used in a commercial product without a separate agreement. The line between those two classes is soft.

## Summary counts

| Class | Sources |
|---|---|
| PERMITTED | 8 (7 RSS publishers covering 7 feeds, plus Google Trends) |
| NON-COMMERCIAL | 22 publishers (31 feed entries) |
| LICENCE NEEDED | 5 (4 RSS publishers covering 7 feeds, plus Reddit) |
| UNCLEAR | 14 (13 RSS publishers covering 14 feeds, plus FAA) |

## Summary table

| Publisher | Feeds | Class | Key quote | URL read |
|---|---|---|---|---|
| Federal Reserve Board | 1 | PERMITTED (cite) | "Unless otherwise indicated, information on Board's website is in the public domain and may be copied and distributed without permission. Please cite to the Board as the source of the information." | https://www.federalreserve.gov/disclaimer.htm |
| ECB | 1 | PERMITTED (cite, accuracy) | "users of this website may make free use of the information obtained directly from it subject to the following conditions: When such information is distributed or reproduced, it must appear accurately and the ECB must be cited as the source." | https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html |
| SEC | 1 | PERMITTED (cite, no seal) | "Information presented on sec.gov is considered public information and may be copied or further distributed by users of the web site without the SEC's permission." / "Please consider appropriate citation to the SEC as the source." | https://www.sec.gov/about/privacy-information (read with WebFetch; curl got HTTP 403) |
| White House | 1 | PERMITTED | "Pursuant to federal law, government-produced materials appearing on this site are not copyright protected." (Third-party content on the site is under CC BY 3.0.) | https://www.whitehouse.gov/copyright/ |
| CFTC | 1 | PERMITTED (acknowledge) | "Government information at the CFTC website is in the public domain. Public domain information may be freely distributed and copied, but it is requested that in any subsequent use the CFTC be given appropriate acknowledgement." | https://www.cftc.gov/WebPolicy/index.htm |
| BLS | 1 | PERMITTED (cite) | "everything that we publish, both in hard copy and electronically, is in the public domain" (except previously copyrighted photos and illustrations); "You are free to use our public domain material without specific permission." | https://www.bls.gov/opub/copyright-information.htm (read with WebFetch; curl got HTTP 403) |
| UN News (United Nations) | 1 | PERMITTED (conditional: credit and advise the UN) | "News-related material can be used as long as the appropriate credit is given and the United Nations is advised." (The general UN Terms of Use say "personal, non-commercial use"; see notes.) | https://www.un.org/en/about-us/copyright ; https://www.un.org/en/about-us/terms-of-use |
| Google Trends "Trending now" RSS (Trending lane) | n/a | PERMITTED (cite Google) | "You can use any information from Google Trends, subject to the Google Terms of Service. If you reuse Trends data, attribute the information to Google with a citation." | https://support.google.com/trends/answer/4365538 ; https://policies.google.com/terms |
| BBC | 2 | LICENCE NEEDED | "For business use of our RSS feeds you'll need to get our permission, and there may be a fee to pay." / "You'll need a licence to use our metadata (such as images, text, media and the links to them). Apply for a metadata licence." | https://www.bbc.co.uk/usingthebbc/terms-of-use/ (section 15, "Metadata and RSS feeds") |
| New York Times | 3 | LICENCE NEEDED (NYT Licensing) | "We allow the use of NYTimes.com RSS feeds for personal use in a news reader or as part of a non-commercial blog." / "Commercial use of the Service is prohibited without prior written permission from NYT which may be requested by contacting us at: https://nytlicensing.com/contact/" | https://www.nytimes.com/rss |
| Le Monde (English) | 1 | LICENCE NEEDED (syndication@lemonde.fr) | "The use of Le Monde.fr RSS feeds is intended for strictly personal, private and non-collective use ... Any use of this content in a professional or commercial context or any marketing of this content to third parties is forbidden, except with the express agreement of the Société Editrice du Monde, which can be requested at the following address: syndication@lemonde.fr." | `<copyright>` element of https://www.lemonde.fr/en/rss/une.xml (the lemonde.fr web pages served a bot challenge) |
| Bank of Japan | 1 | LICENCE NEEDED (prior permission from the PR Dept) | "The information included in the site may be copied or reproduced with the exceptions below. When copying or reproducing, the source, the Bank of Japan, should be explicitly credited. i. The copying or reproduction of the content for commercial purposes. ... In such cases, please contact the Public Relations Department of the Bank (post.prd8@boj.or.jp) in advance to request permission." | https://www.boj.or.jp/en/about/copyright.htm |
| Reddit public JSON / OAuth API (Trending lane) | n/a | LICENCE NEEDED (separate agreement with Reddit) | "If you are interested in using the Data APIs for commercial purposes, research in excess of rate limits, or for any use that is not expressly permitted under the Data API Terms, then you will need to enter into a separate agreement with Reddit." | https://redditinc.com/policies/data-api-terms ; https://redditinc.com/policies/developer-terms (s. 4.1) |
| NPR | 1 | NON-COMMERCIAL | "To use Content Feeds, API content and podcasts ... you agree to the following conditions and acknowledgments: the content may be used only for personal noncommercial use or for noncommercial online use by a 501(c)(3) Nonprofit Corporation (which is not a news, broadcast, or multimedia organization)" | https://www.npr.org/about-npr/179876898/terms-of-use |
| Al Jazeera | 1 | NON-COMMERCIAL | "In accessing the Service you agree that you do so only for your own personal, non-commercial use." | https://www.aljazeera.com/terms-and-conditions |
| CNBC (Versant) | 5 | NON-COMMERCIAL (CNBC-specific terms page not readable) | "We grant you a limited, non-sublicensable, non-transferable license to access and use the Versant Services for your personal and non-commercial use only" | https://versantmedia.com/terms (cnbc.com pages returned HTTP 403) |
| MarketWatch (Dow Jones) | 2 entries, 1 URL | NON-COMMERCIAL | "9.1 The Services are for your individual, personal and non-commercial use only. Thus, you may not access or use the Content, including without limitation, any Content made available through one of our RSS feeds, in any commercial product or service, without our express written consent." | https://www.dowjones.com/terms-of-use/ |
| Bloomberg | 3 | NON-COMMERCIAL | "You agree to use the Service solely for your own noncommercial use and benefit, and not for resale or other transfer or disposition to, or use by or for the benefit of, any other person or entity." | https://www.bloomberg.com/notices/tos/ |
| The Guardian | 3 | NON-COMMERCIAL | "You can use these feeds in a number of ways for personal, non-commercial purposes in accordance with our terms of service." | https://www.theguardian.com/help/feeds ; https://www.theguardian.com/help/terms-of-service |
| Cointelegraph | 1 | NON-COMMERCIAL | "you may only download the content ... for your own personal non-commercial use. Except as set out above, you are not permitted to copy, reproduce ... transmit ... the Content for any other purpose whatsoever without the prior written permission of Cointelegraph." | https://cointelegraph.com/terms-and-privacy (s. 17) |
| Decrypt | 1 | NON-COMMERCIAL | The terms define the Services to include "RSS feeds, APIs, and Software"; "The Services shall be used only in a noncommercial manner." | https://decrypt.co/terms-of-service |
| The Block | 1 | NON-COMMERCIAL | Excerpts only: "for your own personal use"; you must not "exploit any Content for a commercial purpose" (s. 04); "you may not share that feed or use it in any application other than an RSS feed reader" (s. 07). | https://www.theblock.co/terms-of-service (read with WebFetch, which returns short excerpts; curl got HTTP 403) |
| OilPrice.com | 1 | NON-COMMERCIAL | "The information provided on the Oilprice.com website is for personal, non-commercial use." / "Content ... may be searched, retrieved, displayed, downloaded, and printed by Users for personal use only." | https://oilprice.com/terms-and-conditions |
| France 24 (France Médias Monde) | 1 | NON-COMMERCIAL | "France Médias Monde grants the User non-transferable, free, non-exclusive, personal and private authorisation to use the Services and the Websites solely for personal and private use" (the GTU lists www.france24.com among the Websites) | https://francemediasmonde.com/legal-notice |
| Bank of England | 1 | NON-COMMERCIAL (permission form for anything else) | "You may ... download, display or print the Resources for personal use or internal use within an individual organisation for non-commercial purposes. The Bank typically grants permission for non-commercial re-use of the Resources" | https://www.bankofengland.co.uk/legal |
| CNA (Mediacorp) | 1 | NON-COMMERCIAL | "This service is free and for personal, non-commercial use." | https://www.channelnewsasia.com/rss |
| NBC News (NBCUniversal) | 1 | NON-COMMERCIAL (personal use) | "We grant you a limited, non-sublicensable, non-transferable license to access and use the NBCUniversal Services for your personal use ... no element of the Content may be used or exploited in any way other than as part of the authorized NBCUniversal Services" | https://www.nbcuniversal.com/terms (linked from the nbcnews.com footer) |
| CBS News (Paramount) | 1 | NON-COMMERCIAL | "You are only permitted to access and view the Content for personal, non-commercial purposes in accordance with these Terms, and may not build a business or other enterprise utilizing any of the Content, whether for profit or not." | https://legal.paramount.com/us/en/cbsi/terms-of-use |
| Axios | 1 | NON-COMMERCIAL | "we grant you permission to access and use the Services and the Content for your personal and non-commercial use only." | https://legal.axios.com/terms-of-use |
| The National (UAE) | 1 | NON-COMMERCIAL | "Material on these platforms is solely for your personal, non-commercial use." | https://www.thenationalnews.com/terms-and-conditions/ |
| The War Zone (Recurrent) | 1 | NON-COMMERCIAL | "These Terms and Conditions permit you to use the Websites for your personal use only. You must not, without the prior written consent of the Company: ... Access or use for any commercial purposes any part of the Websites" | https://recurrent.io/terms-and-conditions/ (linked from the twz.com footer) |
| Breaking Defense (Breaking Media) | 1 | NON-COMMERCIAL | "You are permitted to use the Website for your personal, non-commercial use only or legitimate business purposes related to your role as a current or prospective customer, supplier or distributor of the Company." | https://breakingdefense.com/terms-of-service/ |
| Defense One (GovExec) | 1 | NON-COMMERCIAL | "These Sites are provided solely for your personal, non-commercial use." / "Any use for a commercial or public purpose requires specific written permission from GEMG." | https://www.govexec.com/about/terms-and-conditions/ (linked from the defenseone.com footer) |
| Fox Business | 1 | NON-COMMERCIAL | "FOX offers free headlines for personal, non-commercial use via Really Simple Syndication ("RSS")" | https://www.foxbusiness.com/fox-business-rss-feed ; https://www.foxbusiness.com/terms-of-use |
| Seeking Alpha | 1 | NON-COMMERCIAL | "use of the Content for any purpose other than your own personal, noncommercial use is a violation of our copyright and other proprietary rights" | https://about.seekingalpha.com/terms |
| Financial Times | 2 | UNCLEAR (could not read) | The feed itself says: "See http://www.ft.com/servicestools/help/terms#legal1 for the terms and conditions of reuse." | help.ft.com returned HTTP 503 to curl and WebFetch could not fetch it. Feed `<copyright>` read at https://www.ft.com/markets?format=rss |
| CoinDesk | 1 | UNCLEAR (could not read) | none read | https://www.coindesk.com/terms (HTTP 403) |
| CryptoSlate | 1 | UNCLEAR (could not read) | none read | https://cryptoslate.com/terms-of-service/ and /terms/ (HTTP 403) |
| Bitcoin Magazine | 1 | UNCLEAR (terms silent on RSS and commercial use) | "Unauthorized reproduction, distribution, or modification is prohibited without our prior written consent." | https://bitcoinmagazine.com/terms-of-use |
| The Defiant | 1 | UNCLEAR (could not read) | The feed says "All rights reserved, The Defiant". | https://thedefiant.io/terms-of-service (HTTP 404); home page HTTP 403 |
| Unchained | 1 | UNCLEAR (could not read) | none read | https://unchainedcrypto.com/terms/ and /terms-of-service/ (HTTP 403) |
| Politico | 1 | UNCLEAR (could not read) | none read | https://www.politico.com/terms-of-service and /rss (HTTP 403; WebFetch refused the domain) |
| ABC Australia | 1 | UNCLEAR (could not read) | The feed says "Copyright 2026, Australian Broadcasting Corporation. All Rights Reserved." | https://help.abc.net.au/hc/en-us/articles/360001548096-ABC-Terms-of-Use (HTTP 403) |
| DW (Deutsche Welle) | 1 | UNCLEAR (could not read) | none read | https://www.dw.com/en/legal-notice/a-63500643 (renders only with JavaScript; WebFetch refused the domain) |
| Mining.com | 1 | UNCLEAR | The "Policies & Terms" footer link opens a subscription licence for Northern Miner and CMJ products ("solely for Licensee's internal business purposes"). The sister site's copyright notice says "may be used only for your personal non-commercial purposes", but that page is for The Northern Miner, not Mining.com. | https://www.northernminer.com/terms-conditions/ ; https://www.northernminer.com/copyright/ |
| gCaptain | 1 | UNCLEAR (no terms page found) | The footer links only About and Privacy Policy. Neither page mentions content reuse. | https://gcaptain.com/about/ ; https://gcaptain.com/about/privacy-policy/ |
| Meduza | 1 | UNCLEAR (silent on RSS and commercial use) | "You cannot republish our content without prior approval from our newsroom." / "Meduza regularly releases its content under a Creative Commons CC BY license ... you must identify the author (s) and source. Please note that this license does not apply to photographs." | https://meduza.io/en/pages/about |
| The Moscow Times | 1 | UNCLEAR (no terms found) | The RSS page and the privacy policy say nothing about reuse or commercial use. No terms-of-use page was found. | https://www.themoscowtimes.com/page/rss ; https://www.themoscowtimes.com/page/privacy-policy |
| FAA NAS status (nasstatus.faa.gov, Trending lane) | n/a | UNCLEAR (no copyright statement found) | The only notice found is the system-use banner: "You are accessing a U.S. Government information system ... provided for U.S. Government-authorized use only." | https://www.faa.gov/web_policies ; https://www.faa.gov/web_policies/disclaimer ; https://nasstatus.faa.gov/ |

## Notes

### Counting
- There are 59 feed entries and 46 RSS publishers. MarketWatch appears twice, in desk-default and desk-extra, with the same URL `feeds.content.dowjones.io/public/rss/mw_topstories`.
- There are 5 CNBC entries: ids 10000664, 20910258, 15839069, 100727362 and 19836768.
- Bloomberg, NYT and The Guardian have 3 entries each. BBC and FT have 2 each.
- Feed totals by class: PERMITTED 7, NON-COMMERCIAL 31, LICENCE NEEDED 7, UNCLEAR 14. That adds up to 59.

### PERMITTED sources and their conditions
- **Federal Reserve, SEC, BLS, CFTC**: cite or acknowledge the agency as the source.
- **SEC**: do not use the SEC seal or logos.
- **BLS**: previously copyrighted photos and illustrations are excluded.
- **ECB**: the information must appear accurately and the ECB must be cited. Two further conditions in the same clause are quoted in the table. The clause also covers information incorporated into documents that are sold: the publisher must inform buyers before they pay.
- **White House**: government-produced material is not copyright protected. Third-party content on the site is under CC BY 3.0.
- **Google Trends**: cite Google ("Data source: Google Trends"). Use is subject to the Google Terms of Service. Those terms forbid "using automated means to access content from any of our services in violation of the machine-readable instructions on our web pages (for example, robots.txt files ...)". The robots.txt at trends.google.com, checked today, disallows only `/explore?` and `/trends/explore?`. It does not disallow `/trending/rss`.
- **UN News**: the UN copyright page carves out news material: "News-related material can be used as long as the appropriate credit is given and the United Nations is advised." The general "Terms and Conditions of Use of United Nations Web Sites" grant use only "for the User's personal, non-commercial use, without any right to resell or redistribute". The two pages conflict. The PERMITTED class rests on the specific news carve-out. That carve-out has two conditions: give credit, and advise the UN, meaning tell them about the use.

### LICENCE NEEDED details
- **BBC**: section 15 also says "You're not allowed to pluck metadata from our content or RSS feeds." The personal permission ("You can add the BBC News RSS feed to your website or social media account") requires that you not change the feed. It also requires a credit and hyperlink to BBC News. Business use is handled separately and needs BBC permission, possibly for a fee.
- **NYT**: the route named is nytlicensing.com/contact.
- **Le Monde**: the route named is syndication@lemonde.fr. The English site footer also links to https://www.lemonde.fr/en/syndication/, but that page could not be read because of a bot challenge.
- **Bank of Japan**: commercial copying needs prior permission from the Public Relations Department (post.prd8@boj.or.jp). No fee is mentioned. The site also says: "The contents of the site may not be altered or erased without the prior permission of the Bank."
- **Reddit**: the Developer Terms (s. 4.1) also forbid, without written approval, using Reddit data in anything that is monetized, or to "derive revenues of any kind from any portion of Reddit Services and Data, whether directly or indirectly". This applies to both the OAuth API and the public JSON endpoints, because both are Reddit Data APIs.

### NON-COMMERCIAL caveats
- **CNBC**: the Versant Terms of Service say CNBC is also governed by separate "CNBC Terms of Service". Every cnbc.com page returned HTTP 403, so the CNBC-specific text was not read. The class rests on the Versant general licence.
- **The Block**: WebFetch returned only excerpts, not complete sentences. The RSS clause sits under "Restricted access" (s. 07) and may apply to restricted-content feeds. The commercial-purpose ban is in s. 04, "Your Use of the Service".
- **Guardian**: commercial API access is a separate route, the Guardian Open Platform licence. Even under that licence, s. 7(a) lets you show content to end users "strictly for their personal and non-commercial use only", and s. 8 requires you to display any ads the Guardian supplies. See https://www.theguardian.com/open-platform/terms-and-conditions.
- **Bank of England**: the page invites requests through a "request permission to use Bank of England material" form. The Bank of England Database (statistics) is a different case: it is under the UK Open Government Licence, which allows commercial reuse with attribution. That does not cover the news RSS feed.
- **NPR**: its terms also forbid uses that suggest NPR endorses a product, and they exclude e-newsletters.
- **CNA**: the Indonesian edition's RSS terms (https://www.cna.id/rssterms) say the same: free, personal and non-commercial only. They add that the feed may be used only with a working link straight to the full article, and that ads may not be placed against the feed content.

### UNCLEAR: what was tried
- **Pages blocked by bot protection** (HTTP 403 or 503 to curl; WebFetch refused or got 403): CoinDesk, CryptoSlate, Unchained, The Defiant, Politico, ABC Australia, FT (help.ft.com), DW (JavaScript only), CNBC.
- An attempt to read the blocked pages through the Wayback Machine was refused by the session's permission check, so it was not used. Web searches did not surface the operative text for any of these.
- **Secondary leads, not verified on the publishers' own current pages** (do not rely on them):
  - Archived FT copyright policy (2014-2015, via a third-party archive in search results): it reportedly said feeds may not be used "as part of a paid for service or for other commercial gain", and that FT licenses organisations that "wish to systematically republish our headlines as part of a commercial service".
  - A third-party blog says ABC News feeds are public "for non-commercial use".
- **Bitcoin Magazine and Meduza**: the terms were read, but they say nothing about RSS or commercial use. Both reserve republication of content: Bitcoin Magazine needs prior written consent, and Meduza needs prior approval except for select CC BY items (photographs excluded).
- **Mining.com**: no Mining.com-specific terms of use were found. /terms-of-use/, /terms-and-conditions/, /copyright/ and /republishing-license/ all returned 404.
- **FAA**: no copyright or reuse statement was found on faa.gov's Web Policies or Disclaimer pages, or on the nasstatus.faa.gov home page. No API terms of use were found for the NAS status endpoint either. It is a federal agency source, but because no statement saying it has no restrictions was found, it is marked UNCLEAR rather than assumed.

### Method
- Pages were fetched with curl using a browser user-agent and converted to text locally. The operative sentences were extracted by keyword: RSS, commercial, personal, reproduce, copyright.
- Where curl was blocked, WebFetch was tried. That is how SEC, BLS and The Block were read.
- The `<copyright>` element of each feed was also checked. That is how the Le Monde terms were found, and how the FT pointer was found.
- All quotes above are verbatim from the pages listed, except The Block, which is limited to excerpts.
