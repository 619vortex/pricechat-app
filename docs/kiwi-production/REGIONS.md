# Kiwi VPN: new regions (provider options, costs, legal notes)

Date: 2026-11-10. **Nothing has been ordered.** Prices are public list prices read today, in USD, net of VAT (EUR converted at
1.16). Vultr's prices come from its public API (its website blocked automated reading). Each provider's order page shows the
final price. The same data is machine-readable in `vpn-node/infra/regions.json`, and
`node vpn-node/infra/regions.mjs plan [--wave N | --all | keys]` prints the steps and totals (dry run; it can't order anything).

## Summary

- **Hetzner has only DE, FI, US (Ashburn, Hillsboro) and Singapore.** Every other requested country needs a second provider,
  which also fixes the biggest single point of failure (one Hetzner account; INFRA-AUDIT.md S1).
- **Second provider: Vultr.** One account covers 7 of the 9 countries (London, Amsterdam, Tel Aviv, Singapore, Tokyo, Toronto,
  Sydney) at **$5/month** each: 1 vCPU, 1 GB, 1 TB transfer, IPv4 + IPv6, hourly billing, extra traffic $10/TB.
  **Condition:** Vultr's acceptable use policy couldn't be read today: get Vultr's written confirmation that a commercial VPN
  is allowed before ordering.
- **No mainstream provider has Cyprus or the UAE.** EDIS Global has both (and explicitly allows commercial VPNs), at about $15.
- **UAE: avoid** until a UAE lawyer confirms it's allowed (licensing, cybercrime law; the owner lives in Dubai).

## Cost table

Monthly, per new server, at 1 TB and 3 TB of outgoing traffic per server.

| Key | Country / city | Provider, plan | 1 TB | 3 TB | Wave | Recommendation | Risk |
|---|---|---|---|---|---|---|---|
| lon1 | UK, London | Vultr vc2-1c-1gb | $5.00 | $25.00 | 1 | **go** | medium |
| ams1 | Netherlands, Amsterdam | Vultr vc2-1c-1gb | $5.00 | $25.00 | 1 | **go** | low |
| sgp1 | Singapore | Vultr vc2-1c-1gb | $5.00 | $25.00 | 1 | **go** | low |
| nrt1 | Japan, Tokyo | Vultr vc2-1c-1gb | $5.00 | $25.00 | 2 | go | medium |
| tor1 | Canada, Toronto | Vultr vc2-1c-1gb | $5.00 | $25.00 | 2 | go | medium |
| syd1 | Australia, Sydney | Vultr vc2-1c-1gb | $5.00 | $25.00 | 2 | go | medium |
| tlv1 | Israel, Tel Aviv | Vultr vc2-1c-1gb | $5.00 | $25.00 | 3 | wait | medium |
| lca1 | Cyprus, Limassol | EDIS KVM Starter 1/2 GB | $15.10 | $29.02 | 3 | wait | medium |
| dxb1 | UAE, Dubai | EDIS KVM Starter 1/2 GB | $15.10 | $29.02 | - | **avoid** | high |

| Scenario | New servers, 1 TB each | New servers, 3 TB each | Whole fleet incl. today's $40.77 (1 TB) |
|---|---|---|---|
| **Recommended first wave** (London, Amsterdam, Singapore) | **$15.00** | $75.00 | **$55.77** |
| Waves 1 + 2 (+ Tokyo, Toronto, Sydney) | $30.00 | $150.00 | $70.77 |
| Eight regions (everything except UAE) | $50.10 | $204.02 | $90.87 |
| **All nine requested countries** | **$65.20** | **$233.04** | **$105.97** |

Realistic traffic today is a few GB per server per month, so the 1 TB column is the likely bill for months. The 3 TB column is
the "busy" case; at that point a provider with cheaper extra traffic pays off (Linode $5/TB, Kamatera 5 TB included, OVH
unmetered in UK/Canada). Ashburn's own overage stays $1.20/TB (Hetzner).

## Country by country

Options cheapest first. "1 TB / 3 TB" = server plus extra traffic to reach that much per month. IPv4 included unless noted.

### UK (London): **go, wave 1**
| Option | 1 TB | 3 TB | Notes |
|---|---|---|---|
| OVHcloud VPS-1 (UK), 2 vCPU / 4 GB | ~$4.54 | ~$4.54 | unmetered at 500 Mbps; monthly term, can't change after delivery |
| Kamatera London, 1 vCPU / 1 GB | $5 ($4 + $1 IPv6) | $5 | 5 TB included; AUP lets them terminate if blacklisted |
| **Vultr London** | **$5** | $25 | recommended (one account for 7 countries) |
| Linode London Nanode | $5 | $15 | old AUP: an IP on an abuse list = violation (risky for VPN exits) |
| DigitalOcean lon1 | $6 | ~$26 | AUP bans "cloaking identity" (grey area) |

Legal: Investigatory Powers Act 2016 (retention and technical-capability notices possible). Online Safety Act: Ofcom is
"looking at" VPNs; no VPN duty enacted. Risk medium.
Sources: [Vultr plans API](https://api.vultr.com/v2/plans?type=vc2), [Vultr overage](https://docs.vultr.com/support/platform/billing/what-is-the-bandwidth-overage-rate), [OVH UK](https://www.ovhcloud.com/en-sg/vps/vps-uk), [Kamatera pricing](https://www.kamatera.com/pricing/), [Akamai pricing](https://www.akamai.com/cloud/pricing/cpu), [Linode AUP](https://linode.com/aup), [DO pricing](https://www.digitalocean.com/pricing/droplets), [DO AUP](https://www.digitalocean.com/legal/acceptable-use-policy), [TechRadar on Ofcom](https://www.techradar.com/vpn/vpn-privacy-security/ofcom-weighs-further-action-on-vpns-following-online-safety-act), [Mishcon](https://www.mishcon.com/news/online-safety-act-vpns-and-age-verification-what-the-house-of-lords-debate-reveals)

### Netherlands (Amsterdam): **go, wave 1**
Kamatera $5 / $5; **Vultr $5 / $25**; Linode $5 / $15; DigitalOcean ams3 $6 / ~$26. (OVH VPS and Lightsail: no Amsterdam.)
Legal: Dutch data-retention law struck down 2015; frequent copyright notices (have an abuse process). Risk low.
Sources: [Vultr regions API](https://api.vultr.com/v2/regions), [EDRi](https://edri.org/our-work/dutch-data-retention-law-struck-down-for-now/)

### Singapore: **go, wave 1**
Kamatera $5 / $5; **Vultr $5 / $25**; Linode $5 / $15; DigitalOcean sgp1 $6 / ~$26; Hetzner CPX02 $9.59 (0.5 TB, $8.30/TB) or
CPX11 $12.09 (1.1 TB). OVH caps Singapore at 500 GB-1 TB then 10 Mbps. Vultr is cheaper than Hetzner here and moves a region
off the Hetzner account; Hetzner `sin1` stays in `catalog.json` as the fully automated alternative.
Legal: PDPA, no registration; IMDA licensing unclear for VPNs; strict copyright enforcement. Risk low.
Sources: [Vultr plans API](https://api.vultr.com/v2/plans?type=vc2), Hetzner API `/pricing` (read 2026-11-10), [Legal500](https://www.legal500.com/guides/chapter/singapore-data-protection-cybersecurity/)

### Japan (Tokyo): **go, wave 2**
Kamatera $5 / $5; **Vultr Tokyo or Osaka $5 / $25**; Linode $5 / $15; Lightsail $7 (2 TB) / $12 (3 TB). No DigitalOcean.
Legal: no data retention. Since 2021 foreign telecom operators serving users in Japan must notify MIC and appoint a local
representative (depends on users, not the server's location): check before marketing to Japan. Risk medium.
Sources: [PwC Japan](https://www.pwc.com/jp/en/knowledge/news/legal-news/legal-20210730-3.html), [MoFo](https://www.mofo.com/resources/insights/210507-enforcement-against-foreign-telecommunications-operators)

### Canada (Toronto): **go, wave 2**
OVH Beauharnois ~$4.54 unmetered; Kamatera $5 / $5; **Vultr $5 / $25**; Linode $5 / $15; DigitalOcean tor1 $6 / ~$26.
Legal: Bill C-22 (Lawful Access Act 2026) passed the House 2026-06-18; Senate status unconfirmed today. It would require
access capabilities and allow metadata-retention rules. Risk medium and rising.
Sources: [LEGISinfo C-22](https://www.parl.ca/LegisInfo/en/bill/45-1/c-22), [Fasken](https://www.fasken.com/en/knowledge/2026/03/the-government-of-canada-introduces-bill-c22)

### Australia (Sydney): **go, wave 2**
Kamatera $5 / $5; **Vultr Sydney or Melbourne $5 / $25**; Linode $5 / $15 (cheapest extra traffic); DigitalOcean syd1 $6 / ~$26;
Lightsail Sydney only 1 TB then $0.114/GB.
Legal: Assistance and Access Act 2018 (technical assistance/capability notices); whether two-year metadata retention covers
VPNs is disputed. Risk medium.
Sources: [Home Affairs FAQ](https://www.homeaffairs.gov.au/nat-security/files/assistance-access-act-faq.pdf), [iTnews](https://www.itnews.com.au/news/confusion-reigns-over-whether-aussie-vpns-must-keep-user-metadata-459657)

### Israel (Tel Aviv): **wait, wave 3**
Kamatera Tel Aviv $5 / $5 (Israeli company, several sites); **Vultr tlv $5 / $25** (confirmed in Vultr's API); EDIS Petah
Tikva ~$8.70 (1 TB) / ~$19.70; Google/Oracle/AWS Israel regions are big-cloud egress prices (not costed). No Lightsail.
Legal: Communications Data Law 2007 (data by court order; 24-hour administrative orders when urgent). Physical/conflict risk
in 2026. Risk medium. Wait: start only if Israeli users ask for it.
Sources: [Vultr Tel Aviv](https://blogs.vultr.com/vultr-tel-aviv), [EDIS Tel Aviv](https://www.edisglobal.com/vps-server/israel-tel-aviv), [ACRI](https://law.acri.org.il/en/2012/05/28/ruling-in-big-brother-petitions/)

### Cyprus (Limassol): **wait, wave 3**
No mainstream provider. HostZealot Limassol ~$8.50 (annual billing, 3-month minimum, IPv6 not stated); **EDIS Limassol
(CYTA Amathounta) Starter $15.10 with 1 TB; 3 TB ≈ $29** (allows commercial VPNs explicitly; monthly, auto-renews).
Legal: EU/GDPR; data-retention law 183(I)/2007 partly struck down 2011 but held in force in 2018 (aimed at public network
operators). Risk medium. Wait: little demand signal yet; Amsterdam/London cover EU content.
Sources: [EDIS Cyprus](https://www.edisglobal.com/vps-server/cyprus-limassol), [EDIS commercial VPN](https://docs.edisglobal.com/faq/commercial-vpn-hosting), [EDIS traffic pool](https://docs.edisglobal.com/faq/traffic-bandwidth/traffic-pool), [HostZealot](https://www.hostzealot.com/vps/city-cy-limassol), [EDRi](https://edri.org/our-work/edrigramnumber9-3data-retention-un-lawful-cyprus/), [FRA](https://fra.europa.eu/es/caselaw-reference/cyprus-supreme-court-cyprus-primary-jurisdiction-civil-application-no-332018)

### UAE (Dubai): **avoid**
Options if ever approved: EDIS Dubai (Equinix DX1) Starter $15.10 / ≈ $29; Gcore Dubai (price unconfirmed); Oracle Dubai/Abu
Dhabi ARM ~$9.5-14 (unconfirmed); Azure UAE North (traffic ~$0.18/GB: uneconomic). **AWS me-central-1 excluded**: two UAE
zones were knocked out by drone strikes on 2026-03-01 and some data was unrecoverable.
Legal, risk high:
- Providing public telecom services needs a **TDRA licence**; unlicensed provision is punishable.
- Federal Decree-Law 34/2021 Art. 10: prison and/or AED 500k-2M fines for using a VPN or another IP to commit or hide a crime.
- VoIP: using a VPN to reach blocked VoIP may be illegal.
- No source found on whether a foreign service running an exit node is lawful; local hosts' AUPs (e&, du, Khazna) unchecked.
- The owner lives in Dubai: the legal exposure is personal. **A UAE lawyer's written opinion first.**
Sources: [EDIS Dubai](https://www.edisglobal.com/vps-hosting/united-arab-emirates-dubai), [Hadef Partners](https://hadefpartners.com/news-insights/insights/vpn-and-voip/), [DLA Piper](https://www.dlapiperintelligence.com/telecoms/countries/united-arab-emirates/enforcement.html), [Khaleej Times](https://www.khaleejtimes.com/uae/legal/is-vpn-banned-in-uae-rules-fines-what-you-need-to-know), [Gulf News](https://gulfnews.com/uae/government/uae-public-prosecution-warns-against-illegal-network-circumvention-fines-up-to-dh2-million-1.1734973476355), [TeleGeography on UAE damage](https://resources.telegeography.com/war-middle-east-damaged-data-centers-cloud-disruptions), [Techzine](https://www.techzine.eu/news/infrastructure/144322/aws-cant-recover-all-data-from-data-centers-hit-in-middle-east/)

## Provider policies (commercial VPN, abuse)

| Provider | Commercial VPN | Abuse handling |
|---|---|---|
| EDIS Global | explicitly allowed | forwards complaints, expects action within 24 h |
| Vultr | **unconfirmed** (AUP page blocked automated reading): ask support in writing | unconfirmed |
| DigitalOcean | not mentioned; bans open proxies, Tor exits and "cloaking identity" | forwards DMCA, may disable the server |
| Linode/Akamai | not mentioned; old AUP treats an abuse-listed IP as a violation | strict |
| Kamatera | not mentioned; may terminate if blacklisting is likely | reported weak |
| Hetzner | tolerated (Tor community notes) | automated locks on repeated reports |

Abuse readiness for any provider: port 25 is already blocked on nodes; add 465/587 (SECURITY-AUDIT.md L19); publish an abuse
contact; answer forwarded notices within 24 h; keep no logs that could identify a customer.

## How a new region is added and tested (after approval)

Printed per region by `regions.mjs plan`:
1. Order (Hetzner: `kiwi-infra.mjs create` with the owner's typed phrase; others: by hand from the printed spec).
2. Provider firewall: 22, 51820/udp, 80/443, ICMP; 8443 only from Railway's static IPs.
3. DNS `<key>.vpn.pricechat.live` (owner approval).
4. Install from committed files (`install.sh`, `install-proxy.sh`).
5. **On the node**: `vpn-node/infra/node-selftest.sh` (a real WireGuard client in a network namespace: handshake, IPv4 exit,
   the node's resolver, IPv6 refused quickly, no access to the node/metadata/private ranges, port 25 blocked, no client-to-client).
6. Register in Admin as **Testers only**, with the traffic allowance (enables bandwidth and pace alerts).
7. **Through a client**: `node vpn-node/infra/region-verify.mjs --host <key>.vpn.pricechat.live --country <CC>` while connected:
   exit IPv4 = the node (two echo services), country by two geolocation services (WARN only: new IPs lag), resolver identity
   via whoami.akamai.net and the bash.ws leak test (must be the node), IPv6 none or the node's own, HTTPS to 4 sites. Exit code
   = number of FAILs. Plus the Windows CI e2e job (exit IP, bash.ws DNS, api6 IPv6, reconnect, switch) when the shared test
   account's device bucket is free.
8. The owner switches it to everyone.

A new IP often geolocates to the provider's home country for days or weeks: if `country` warns, ask the provider to update its
geofeed (RFC 8805) before advertising the country.
