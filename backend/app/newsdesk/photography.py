"""Photography first (owner, 2026-11-19, second brief: "STOP MAKING FINANCIAL INFOGRAPHICS FOR EVERY NEWS POST ... For gold news, show
GOLD. For Bitcoin, show BITCOIN. For Microsoft, show MICROSOFT. For oil, show OIL ... I WANT BEAUTIFUL NEWS PHOTOGRAPHY, NOT BLOOMBERG
TERMINAL SCREENSHOTS.")

The picture is a real, licensed photograph of what the story is about; the caption carries the news and the numbers. Charts stay only
where the story is a comparison the eye needs (one asset against another, the week's winners and losers: app/newsdesk/clean).

Two sources, in this order:
1. The subject library (LIBRARY): photographs on Wikimedia Commons chosen by an editor for the subjects the desk covers most (gold,
   silver, Bitcoin, Ether, oil, the New York Stock Exchange, Microsoft, Nvidia, Apple, Tesla), each looked at before it went in: real
   photographs (no renders, logos files or illustrations), the subject unmistakable, strong composition, dark or rich backgrounds where
   possible, no watermark, at least 2,000 px wide. The licence is NOT taken on trust: every time, the file's current record is read from
   Commons and passes photos.verify_licence (CC0, public domain, CC BY / BY-SA only), and the credit drawn on the picture is the record's
   own author and licence. The library rotates (the least recently used photo of that subject goes next), so gold news does not show
   the same nugget every day. These are pictures OF the subject, never presented as pictures of an event: the desk never fabricates or
   stands in for event photography (a war, a strike, a disaster gets a dated photograph of the event itself or none: editor.brief).
2. Search (clean.find_photo, for everything else): licensed photos whose record shows the subject (app/newsdesk/editor.depiction),
   ranked here by how they look (`aesthetic`: a dark, quiet background, rich colour, contrast and detail) after every relevance rule.

Crops: X 1600 x 900 (16:9), Telegram 1280 x 1024 (5:4, larger in a phone's chat), both by photos.smart_crop, which keeps the
saliency box whole and only crops and resizes: nothing is stretched or altered. A source smaller than the frame is never upscaled.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

FRAMES = {"x": (1600, 900), "telegram": (1280, 1024)}
LIBRARY_TTL = 6 * 3600  # Commons records re-read at most every 6 hours (a licence change or a deletion is picked up)
LIBRARY_SHARP_MIN = 35.0  # vetted photos: only a broken or degraded download is refused (a clean white store wall reads 58)
MIN_SOURCE = 1600  # px on the long side: the frame is never upscaled from less


@dataclass(frozen=True)
class Subject:
    key: str
    name: str
    pattern: str  # whole words in a headline that make it this subject's story
    files: tuple[str, ...]  # Commons file titles, best first

    def rx(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.I)


# Each file was looked at (2026-11-19) before it went in; licences are re-verified live. Order is preference.
LIBRARY: tuple[Subject, ...] = (
    Subject("gold", "Gold", r"\b(gold|bullion)\b(?! ?(?:man|men|standard chartered|coast|cup|medal))", (
        "File:Gold nugget (Australia) 4 (16848647509).jpg",
        "File:Gold nuggets (placer gold) (Central City District, Gilpin County, Colorado, USA) 3 (17070810462).jpg",
        "File:Gold bar of Banque de France.jpg",
        "File:Gold fluvial pebbles (placer gold) (Washington State, USA) 4 (16846562679).jpg",
    )),
    # owner, 2026-11-20: "beautiful, real, properly licensed bullion bars, silver stacks and metal photography. Reject cluttered
    # collector-coin images." Each looked at in both frames (X 16:9, Telegram 5:4) and run through this module's own checks on 2026-11-20:
    # licence (CC BY / BY-SA, live), size (2,400-5,760 px), sharpness. The Rostov collector coins (owls, a lynx, a cheque, a carpet) are out.
    Subject("silver", "Silver", r"\bsilver\b(?! ?(?:lake|lining|screen|spring|legacy))", (
        "File:Scottsdale Mint 100 Troy Ounce Silver Bar.jpg",  # a 100 oz bar standing on stacked cast bars, dark and lit
        "File:Two five ounce silver APMEX bars by Gage Skidmore.jpg",  # two minted bars on deep red
        "File:Five five ounce silver APMEX bars by Gage Skidmore.jpg",  # five bars fanned on red
        "File:Silver Bar 01.jpg",  # a heavy cast fine-silver bar
        "File:Three five ounce silver APMEX bars by Gage Skidmore.jpg",  # three bars side by side on dark red
        "File:100 Troy oz. Silver Bullion Bar from Johnson Matthey.jpg",  # a 100 oz Johnson Matthey bar
    )),
    Subject("bitcoin", "Bitcoin", r"\b(bitcoin|btc)\b(?! (?:depot|magazine|suisse|core))", (
        "File:Bitcoin \"challenge coin\".jpg",
        "File:Physical bitcoin statistic coin.JPG",
        "File:$10,000 in $100 USD bills with 25 BTC Gold Casascius coin by Gage Skidmore.jpg",
        "File:Holding Bitcoin cryptocurrency coin.jpg",
    )),
    Subject("ether", "Ether", r"\b(ether|ethereum|eth)\b(?! foundation)", (
        "File:Close-up of a physical Ethereum coin (51002904687).jpg",
    )),
    Subject("oil", "Oil", r"\b(oil|crude|brent|wti|opec\+?|diesel|gasoline)\b", (
        "File:LostHillsPumpjacksSunset.JPG",
        "File:PumpjacksFull.JPG",
        "File:PumpjacksSundown.JPG",
        "File:Alpine Melina (IMO 9387944) North Sea 2016.jpg",
        "File:Oil Tanker in North Sea 3928.jpg",
        "File:Crude Oil Tanker Emerald Shiner at BP Oil Refinery Jetty, Kwinana, September 2021 01.jpg",
    )),
    Subject("microsoft", "Microsoft", r"\b(microsoft|msft|outlook|azure|xbox)\b", (
        "File:Microsoft Store @ Metropolis (32458694837).jpg",
        "File:Microsoft Taiwan Corporation headquarters plate 20161126.jpg",
        "File:Microsoft sign at night at The Bravern Building 1.jpg",
    )),
    Subject("nvidia", "Nvidia", r"\b(nvidia|nvda)\b", (
        "File:Nvidia sign.jpg",
        "File:Computex Taipei ad on TaiNEX 1 20190601b.jpg",
        "File:NVIDIA Headquarters.jpg",
    )),
    Subject("apple", "Apple", r"\b(apple|aapl|iphone|icloud)\b", (
        "File:Apple store regent street london - Flickr - jonrawlinson.jpg",
        "File:Apple logo at dusk (1595556910).jpg",
        "File:Aerial view of Apple Park dllu.jpg",
    )),
    Subject("tesla", "Tesla", r"\b(tesla|tsla|cybertruck)\b", (
        "File:Seattle - Tesla at 435 Westlake N 02.jpg",
        "File:Tesla Showroom Dadeland Mall, 27 November 2023 - Red car.jpg",
    )),
    Subject("dogecoin", "Dogecoin", r"\b(dogecoin|doge)\b", (
        "File:Dogecoin gold coin black background 1 - tails.jpg",
        "File:Dogecoin gold coin on wood 3 - tails.jpg",
        "File:Dogecoin gold coin with euro coins wood background 2 - tails.jpg",
    )),
    Subject("natgas", "Natural gas", r"\b(natural gas|lng|gas prices?|henry hub)\b", (
        "File:2023-05-20 01 LNG tanker, GULF ENERGY - IMO 7390143.jpg",
        "File:Aristarchos (LNG Tanker).jpg",
        "File:LNG Tanker \"Gaslog Saratoga\", Melkøya, Norway.jpg",
    )),
    Subject("copper", "Copper", r"\bcopper\b", (
        "File:Bundles of cathode copper.png",
    )),
    Subject("grains", "Wheat", r"\b(wheat|grains?)\b", (
        "File:A wheat field before harvest.jpg",
        "File:Sunset-over-the-wheat-field-featured.jpg",
        "File:Wheat harvest on the Palouse.jpg",
        "File:Wheat close-up.JPG",
    )),
    Subject("fed", "The Federal Reserve", r"\b(federal reserve|fomc|(?-i:Fed)(?:'s|’s)?)\b", (
        "File:Eccles Building (26088200676).jpg",
        "File:Marriner S. Eccles Federal Reserve Board Building.jpg",
        "File:Eccles Building - north side.JPG",
    )),
    Subject("ecb", "The European Central Bank", r"\b(ecb|european central bank)\b", (
        "File:Seat of the European Central Bank and Frankfurt Skyline at dawn 20150422 1.jpg",
        "File:Frankfurt am Main, Seat of the European Central Bank (Skytower (ECB)).jpg",
        "File:Frankfurt EZB.Nordwest-2.20141228.jpg",
        "File:Europäische Zentralbank Frankfurt.jpg",
    )),
    Subject("amazon", "Amazon", r"\b(amazon(?! rainforest| basin| river| forest| region|ia)|amzn|aws)\b", (
        "File:Amazon Spheres at night from 7th and Lenora.jpg",
        "File:2024-12-21, Amazon Spheres (Seattle), 170559.jpg",
        "File:Amazon Spheres 01.jpg",
    )),
    Subject("google", "Google", r"\b(google|alphabet|googl)\b", (
        "File:GoogleTorontoOffice3.jpg",
        "File:GoogleTorontoOffice4.jpg",
        "File:Google 1600 Amphitheatre (4136750386).jpg",
        "File:Google Headquarters in Ireland Building Sign.jpg",
    )),
    Subject("samsung", "Samsung", r"\bsamsung\b", (
        "File:Samsung felirat egy épület tetején, Budapest, 2025.jpg",
    )),
    Subject("tsmc", "TSMC", r"\b(tsmc|taiwan semiconductor)\b", (
        "File:Building of Taiwan Semiconductor Manufacturing Fab 12B at night.jpg",
        "File:Building of Taiwan Semiconductor Manufacturing Fab 12B at dusk1.jpg",
        "File:TSMC Fab 12A 01.jpg",
    )),
    Subject("jpmorgan", "JPMorgan Chase", r"\b(jpmorgan|jp morgan|chase bank)\b", (
        "File:JPMorgan Chase - 270 Park Avenue (55283843744).jpg",
    )),
    Subject("nasdaq", "Nasdaq", r"\bnasdaq\b", (
        "File:Nasdaq MarketSite (51494550508).jpg",
        "File:NASDAQ Times Square - panoramio.jpg",
        "File:NASDAQ Market Site 201506.jpg",
    )),
    Subject("shipping", "Trade and shipping", r"\b(tariffs?|trade war|trade deal|shipping|container ships?|freight|supply chains?)\b", (
        "File:MAERSK HANOI Container Ship (Port Koper SIKOP, 2023).jpg",
        "File:Port Jersey container ship sunset 2018.jpg",
        "File:MSC HARMONY III (container ship, 2006) in Port Koper (SIKOP, 2024).jpg",
        "File:Container ship NYK Themis at the Port of Los Angeles.jpg",
    )),
    Subject("power", "The power grid", r"\b(power grid|blackouts?|power outages?|electricity|power prices?)\b", (
        "File:Deep orange sunset.jpg",
        "File:High Voltage - panoramio.jpg",
        "File:Power Sunset (48755830892).jpg",
    )),
    Subject("binance", "Binance", r"\bbinance\b", (
        "File:2022 - Press Conferences BE6 1591 (52472274614).jpg",
        "File:2022 - Q&A PO1 4700 (52471974491).jpg",
        "File:2022 - Press Conferences BE6 1583 (52472585725).jpg",
    )),
    Subject("stocks", "Wall Street", r"\b(s&p 500|dow jones|wall street|nyse|new york stock exchange|u\.s\. stocks|us stocks)\b", (
        "File:Sign of the New York Stock Exchange, Broad Street.jpg",
        "File:New York Stock Exchange Facade.JPG",
        "File:NEW YORK STOCK EXCHANGE 20240521.jpg",
    )),
)
# -------------------------------------------------------------------------------------------------- people (owner, 2026-11-19)
# "Trump/Xi/Zelensky story -> relevant real photograph of the people or event." Leaders and the market's most-named people, each file looked
# at before it went in (official White House photos are public domain; Kremlin, Web Summit and Commons photographers' files are CC BY/BY-SA/
# CC0, re-verified live). A pair is used when the story names both; otherwise the person named first. CAPTIONS says who is pictured, where
# and when, drawn with the credit: an archive photo never passes for today's meeting. Never in a story of violence (clean.picture).
CAPTIONS: dict[str, str] = {
    "File:Donald Trump visit to China, May 2026 P20260514DT-1182.jpg": "Xi Jinping and Donald Trump in Beijing, May 2026",
    "File:President Donald Trump greets Chinese President Xi Jinping before a bilateral meeting at the Gimhae International Airport terminal (54890669438).jpg": "Donald Trump and Xi Jinping in Busan, October 2025",
    "File:Donald Trump visit to China, May 2026T-0783.jpg": "Xi Jinping and Donald Trump in Beijing, May 2026",
    "File:P20260924DT-2338 (55563017265).jpg": "Xi Jinping and Donald Trump at the White House, September 2026",
    "File:P20250815DT-1255 President Donald Trump meets with Russian President Vladimir Putin at Joint Base Elmendorf Richardson in Anchorage, Alaska.jpg": "Donald Trump and Vladimir Putin in Alaska, August 2025",
    "File:President Donald Trump participates in a bilateral meeting with Ukrainian president Volodymyr Zelenskyy P20250625DT-0772.jpg": "Volodymyr Zelensky and Donald Trump in The Hague, June 2025",
    "File:Donald Trump hosts a meeting with Ukrainian President Volodymyr Zelenskyy in the Cabinet Room of the White House on October 17, 2025 - 1.jpg": "Donald Trump and Volodymyr Zelensky at the White House, October 2025",
    "File:President Donald Trump walks with Israeli Prime Minister Benjamin Netanyahu at the Knesset in Jerusalem (54855555063).jpg": "Donald Trump and Benjamin Netanyahu at the Knesset, October 2025",
    "File:President Donald Trump is greeted by Israeli Prime Minister Benjamin Netanyahu and others after disembarking Air Force One at Ben Gurion International Airport (54855297376).jpg": "Donald Trump and Benjamin Netanyahu in Israel, October 2025",
    "File:Xi Jinping and Vladimir Putin at the parade marking the 80th anniversary of the Great Victory at Great Patriotic War (2025).jpg": "Xi Jinping and Vladimir Putin in Moscow, May 2025",
    "File:P20251106AM-0645 President Donald Trump makes an announcement on lowering drug prices in the Oval Office.jpg": "Donald Trump at the White House, November 2025",
    "File:President Donald Trump participates in a bilateral meeting with Chinese President Xi Jinping at the Gimhae International Airport terminal (54890669548).jpg": "Donald Trump in Busan, October 2025",
    "File:President Donald Trump participates in a bilateral meeting with Chinese President Xi Jinping at the Gimhae International Airport terminal (54890689459).jpg": "Xi Jinping in Busan, October 2025",
    "File:13.05.2025 - Sessão de abertura do IV Fórum CELAC-China (54516194129).jpg": "Xi Jinping in Beijing, May 2025",
    "File:Vladimir Putin in Murmansk (2025-03-27) 2.jpg": "Vladimir Putin in Murmansk, March 2025",
    "File:Владимир Путин - 03 (08-09-2025).jpg": "Vladimir Putin, September 2025",
    "File:Vladimir Putin at award ceremonies (2025-05-22) 07.jpg": "Vladimir Putin, May 2025",
    "File:President Donald Trump participates in a bilateral meeting with President Volodymyr Zelenskyy of Ukraine (54732036979).jpg": "Volodymyr Zelensky at the White House, August 2025",
    "File:11.09 總統出席「第一屆李國鼎獎頒獎典禮」 - 53319959776.jpg": "Nvidia CEO Jensen Huang in Taipei, November 2023",
    "File:Jensen huang stanford 2026-04-30 037.jpg": "Nvidia CEO Jensen Huang at Stanford, April 2026",
    "File:Elon Musk (54817688006).jpg": "Elon Musk, September 2025",
    "File:Elon Musk - 54819827616.jpg": "Elon Musk, September 2025",
    "File:2022 - Press Conferences BE6 1591 (52472274614).jpg": "Binance founder Changpeng Zhao at Web Summit, November 2022",
    "File:2022 - Q&A PO1 4700 (52471974491).jpg": "Binance founder Changpeng Zhao at Web Summit, November 2022",
    "File:2022 - Press Conferences BE6 1583 (52472585725).jpg": "Binance founder Changpeng Zhao at Web Summit, November 2022"
}

PAIRS: tuple[tuple[Subject, str, str], ...] = (  # (subject, first name pattern, second name pattern)
    (Subject("trump_xi", "Donald Trump and Xi Jinping", r"\btrump(?:'s|’s)?\b", (
        "File:Donald Trump visit to China, May 2026 P20260514DT-1182.jpg",
        "File:President Donald Trump greets Chinese President Xi Jinping before a bilateral meeting at the Gimhae International Airport terminal (54890669438).jpg",
        "File:Donald Trump visit to China, May 2026T-0783.jpg",
        "File:P20260924DT-2338 (55563017265).jpg",
    )), r"\btrump(?:'s|’s)?\b", r"(?-i:\bXi(?: Jinping)?\b)"),
    (Subject("trump_putin", "Donald Trump and Vladimir Putin", r"\btrump(?:'s|’s)?\b", (
        "File:P20250815DT-1255 President Donald Trump meets with Russian President Vladimir Putin at Joint Base Elmendorf Richardson in Anchorage, Alaska.jpg",
    )), r"\btrump(?:'s|’s)?\b", r"\bputin(?:'s|’s)?\b"),
    (Subject("trump_zelensky", "Donald Trump and Volodymyr Zelensky", r"\btrump(?:'s|’s)?\b", (
        "File:President Donald Trump participates in a bilateral meeting with Ukrainian president Volodymyr Zelenskyy P20250625DT-0772.jpg",
        "File:Donald Trump hosts a meeting with Ukrainian President Volodymyr Zelenskyy in the Cabinet Room of the White House on October 17, 2025 - 1.jpg",
    )), r"\btrump(?:'s|’s)?\b", r"\bzelensk\w*\b"),
    (Subject("trump_netanyahu", "Donald Trump and Benjamin Netanyahu", r"\btrump(?:'s|’s)?\b", (
        "File:President Donald Trump walks with Israeli Prime Minister Benjamin Netanyahu at the Knesset in Jerusalem (54855555063).jpg",
        "File:President Donald Trump is greeted by Israeli Prime Minister Benjamin Netanyahu and others after disembarking Air Force One at Ben Gurion International Airport (54855297376).jpg",
    )), r"\btrump(?:'s|’s)?\b", r"\bnetanyahu(?:'s|’s)?\b"),
    (Subject("xi_putin", "Xi Jinping and Vladimir Putin", r"(?-i:\bXi(?: Jinping)?\b)", (
        "File:Xi Jinping and Vladimir Putin at the parade marking the 80th anniversary of the Great Victory at Great Patriotic War (2025).jpg",
    )), r"(?-i:\bXi(?: Jinping)?\b)", r"\bputin(?:'s|’s)?\b"),
)
PEOPLE: tuple[Subject, ...] = (
    Subject("trump", "Donald Trump", r"\btrump(?:'s|’s)?\b", (
        "File:P20251106AM-0645 President Donald Trump makes an announcement on lowering drug prices in the Oval Office.jpg",
        "File:President Donald Trump participates in a bilateral meeting with Chinese President Xi Jinping at the Gimhae International Airport terminal (54890669548).jpg",
    )),
    Subject("xi", "Xi Jinping", r"(?-i:\bXi(?: Jinping)?\b)", (
        "File:President Donald Trump participates in a bilateral meeting with Chinese President Xi Jinping at the Gimhae International Airport terminal (54890689459).jpg",
        "File:13.05.2025 - Sessão de abertura do IV Fórum CELAC-China (54516194129).jpg",
    )),
    Subject("putin", "Vladimir Putin", r"\bputin(?:'s|’s)?\b", (
        "File:Vladimir Putin in Murmansk (2025-03-27) 2.jpg",
        "File:Владимир Путин - 03 (08-09-2025).jpg",
        "File:Vladimir Putin at award ceremonies (2025-05-22) 07.jpg",
    )),
    Subject("zelensky", "Volodymyr Zelensky", r"\bzelensk\w*\b", (
        "File:President Donald Trump participates in a bilateral meeting with President Volodymyr Zelenskyy of Ukraine (54732036979).jpg",
    )),
    Subject("jensen_huang", "Jensen Huang", r"\bjensen huang\b|(?-i:\bHuang\b)", (
        "File:11.09 總統出席「第一屆李國鼎獎頒獎典禮」 - 53319959776.jpg",
        "File:Jensen huang stanford 2026-04-30 037.jpg",
    )),
    Subject("musk", "Elon Musk", r"\belon musk\b|(?-i:\bMusk(?:'s|’s)?\b)(?! deer| ox| oxen| melon)", (
        "File:Elon Musk (54817688006).jpg",
        "File:Elon Musk - 54819827616.jpg",
    )),
    Subject("cz", "Changpeng Zhao", r"\bchangpeng zhao\b|\bzhao changpeng\b|(?-i:\bCZ\b)", (
        "File:2022 - Press Conferences BE6 1591 (52472274614).jpg",
        "File:2022 - Q&A PO1 4700 (52471974491).jpg",
        "File:2022 - Press Conferences BE6 1583 (52472585725).jpg",
    )),
)


def people_of(text: str) -> tuple[Subject, int] | None:
    """(the people photograph a headline calls for, where in it they are first named), or None. A pair when both are named (the pair named
    earliest when there are several: "Trump tells Xi before trilateral with Putin" is Trump and Xi), else the person named first."""
    t = text or ""
    pairs = []
    for s, a, b in PAIRS:
        ma, mb = re.search(a, t, re.I), re.search(b, t, re.I)
        if ma and mb:
            pairs.append((max(ma.start(), mb.start()), min(ma.start(), mb.start()), s))
    if pairs:
        _, first, s = min(pairs, key=lambda p: p[0])
        return s, first
    # "Huang" alone is Jensen Huang only beside Jensen or Nvidia (PDD's Colin Huang is not)
    solo = [(m.start(), s) for s in PEOPLE if (m := s.rx().search(t)) and (s.key != "jensen_huang" or re.search(r"jensen|nvidia", t, re.I))]
    if solo:
        at, s = min(solo, key=lambda h: h[0])
        return s, at
    return None




BY_KEY = {s.key: s for s in (*LIBRARY, *PEOPLE, *(p[0] for p in PAIRS))}
# a data post's or market move's symbol -> its subject
SYMBOLS = {"GC=F": "gold", "XAU": "gold", "GOLD": "gold", "PAXG": "gold", "SI=F": "silver", "SILVER": "silver", "BTC-USD": "bitcoin", "BTC": "bitcoin",
           "ETH-USD": "ether", "ETH": "ether", "CL=F": "oil", "BZ=F": "oil", "CL": "oil", "OIL": "oil", "^GSPC": "stocks", "^IXIC": "stocks",
           "^DJI": "stocks", "NG=F": "natgas", "HG=F": "copper", "ZW=F": "grains", "DOGE-USD": "dogecoin", "DOGE": "dogecoin",
           "AMZN": "amazon", "GOOGL": "google", "GOOG": "google", "TSM": "tsmc", "JPM": "jpmorgan", "MSFT": "microsoft", "NVDA": "nvidia", "AAPL": "apple", "TSLA": "tesla"}

_cache: dict[str, tuple[float, Any]] = {}

# photographs that are never a metal's picture, from the library or from search (owner, 2026-11-20: "reject cluttered collector-coin
# images"): coin collections, commemoratives, medals, numismatic displays. A metal story shows the metal: bars, stacks, the metal itself.
CLUTTERED = re.compile(r"\b(coins?|numismatic\w*|commemorative|medals?|proof sets?|banknotes?|collect(?:ion|or|ors|ible|ibles))\b", re.I)
METALS = ("silver", "gold")


def refused(subject_key: str, title: str, description: str = "") -> str:
    """Why this photograph is never the picture of `subject_key`'s story, or ""."""
    if subject_key.lower() in METALS and CLUTTERED.search(f"{title} {description}"):
        return f"a coin or collection picture, not {subject_key} itself (bars, stacks or the metal only)"
    return ""


def subject_of(text: str) -> Subject | None:
    """The library subject a headline is about: the one it names first ("Tesla merger talks put 30,000 bitcoin in focus" is Tesla)."""
    hits = [(m.start(), s) for s in LIBRARY if (m := s.rx().search(text or ""))]
    return min(hits, key=lambda h: h[0])[1] if hits else None


def subject_for_symbol(symbol: str, name: str = "") -> Subject | None:
    key = SYMBOLS.get(str(symbol or "").upper().lstrip("$"))
    return BY_KEY.get(key) if key else subject_of(name)


# -------------------------------------------------------------------------------------------------- how a photograph looks
def aesthetic(img: Image.Image) -> float:
    """0-1, higher is more striking: a dark, quiet surround (the subject lit against it), rich colour, real contrast and fine detail.
    Used only to choose among photos that already passed every relevance and licence rule, never to admit one."""
    small = img.convert("RGB").resize((192, 108))
    a = np.asarray(small, dtype=np.float64)
    lum = a @ np.array([0.299, 0.587, 0.114])
    border = np.concatenate([lum[:12].ravel(), lum[-12:].ravel(), lum[:, :18].ravel(), lum[:, -18:].ravel()])
    dark = 1 - min(1.0, float(np.median(border)) / 200)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    colour = min(1.0, (np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)) / 90)  # Hasler-Suesstrunk
    contrast = min(1.0, float(lum.std()) / 70)
    lap = lum[1:-1, 1:-1] * -4 + lum[:-2, 1:-1] + lum[2:, 1:-1] + lum[1:-1, :-2] + lum[1:-1, 2:]
    detail = min(1.0, float(lap.var()) / 900)
    return round(0.32 * dark + 0.28 * colour + 0.22 * contrast + 0.18 * detail, 4)


# -------------------------------------------------------------------------------------------------- the library
async def _records(client: Any, subject: Subject) -> list[Any]:
    """The subject's files as LicensedPhoto records read from Commons now (cached LIBRARY_TTL), licence-verified."""
    from app.newsdesk import photos

    hit = _cache.get(subject.key)
    if hit and time.time() - hit[0] < LIBRARY_TTL:
        return hit[1]
    params = {"action": "query", "format": "json", "titles": "|".join(subject.files), "prop": "imageinfo",
              "iiprop": "url|size|extmetadata|mime", "iiurlwidth": "2560"}
    r = await client.get(photos.COMMONS, params=params, headers=photos.UA, timeout=photos.TIMEOUT)
    r.raise_for_status()
    pages = {p.get("title"): p for p in ((r.json().get("query") or {}).get("pages") or {}).values()}
    norm = {n.get("from"): n.get("to") for n in (r.json().get("query") or {}).get("normalized") or []}
    out = []
    for title in subject.files:
        page = pages.get(norm.get(title, title)) or pages.get(title)
        if not page or "missing" in page:
            logger.warning("photography: library file %s is gone from Commons", title)
            continue
        p = photos.record_from_commons(page, query=f"library:{subject.key}")
        if p is None:
            continue
        if (no := refused(subject.key, title)):
            logger.warning("photography: library file %s refused: %s", title, no)
            continue
        why = photos.verify_licence(p)
        if why or max(p.width, p.height) < MIN_SOURCE:
            logger.warning("photography: library file %s refused: %s", title, why or "too small")
            continue
        out.append(p)
    _cache[subject.key] = (time.time(), out)
    return out


def _title_of(p: Any) -> str:
    page = str(getattr(p, "page", "") or "")
    from urllib.parse import unquote

    return unquote(page.rsplit("/wiki/", 1)[-1]).replace("_", " ") if "/wiki/" in page else ""


def rotation(records: list[Any], urls: list[str], seed: int = 0) -> list[str]:
    """The library's URLs, least recently used first (never used first of all, in the editor's order)."""
    last: dict[str, str] = {}
    for r in records or []:
        if r and len(r) >= 2:
            last[str(r[0])] = max(last.get(str(r[0]), ""), str(r[1]))
    return sorted(urls, key=lambda u: (last.get(u, ""), urls.index(u)))


async def library_photo(subject: Subject, *, transport: Any = None, records: list[Any] | None = None) -> tuple[Image.Image, dict[str, Any]] | None:
    """The next photograph of the subject from the library, with its verified licence and credit; None when Commons can't be reached
    or every file fails (the caller then searches or goes text only)."""
    import httpx

    from app.newsdesk import editor, photos

    try:
        async with httpx.AsyncClient(transport=transport) as client:
            recs = await _records(client, subject)
            by_url = {p.url: p for p in recs}
            for url in rotation(records or [], list(by_url)):
                p = by_url[url]
                img = await photos.fetch(client, p, min_width=MIN_SOURCE if p.width >= p.height else 0, colour=False)
                if img is None or editor.pixel_problem(img, ui=False, sharp_min=LIBRARY_SHARP_MIN):  # an editor looked at it: only blur and fading can change
                    continue
                meta = p.to_meta()
                who = CAPTIONS.get(str(meta.get("title") or ""), "") or CAPTIONS.get(_title_of(p), "")
                return img, {"url": p.url, "page": meta.get("page", ""), "title": meta.get("title", ""), "credit": p.credit, "who": who,
                             "licence": meta.get("licence", "") or p.licence, "photo": meta, "person": "", "library": subject.key,
                             "editor": {"ok": True, "depiction": "strong", "role": "subject", "subject": subject.name,
                                        "why": f"the editor's {subject.name} photograph", "aesthetic": aesthetic(img)}}
    except Exception as e:  # noqa: BLE001
        logger.info("photography: library %s unavailable (%s)", subject.key, type(e).__name__)
    return None


async def library_self_test(key: str = "silver", transport: Any = None) -> dict[str, Any]:
    """Once after each start, on the server itself (owner, 2026-11-20: "confirm the new silver bullion photos work"): every file of the
    subject read from Commons with its licence re-verified, downloaded, checked like a post's picture (size, sharpness, never refused as
    a coin picture) and framed for X and Telegram. Logged as one line per file ("photography self-test: ..."). Never raises."""
    import httpx

    from app.newsdesk import editor, photos

    subject = BY_KEY[key]
    out: dict[str, Any] = {"subject": key, "files": len(subject.files), "ok": [], "refused": []}
    try:
        async with httpx.AsyncClient(transport=transport) as client:
            recs = await _records(client, subject)
            for p in recs:
                name = _title_of(p) or p.title
                img = await photos.fetch(client, p, min_width=MIN_SOURCE if p.width >= p.height else 0, colour=False)
                why = "download or pixels refused" if img is None else editor.pixel_problem(img, ui=False, sharp_min=LIBRARY_SHARP_MIN) or ""
                if not why:
                    x, _ = frame(img, "x")
                    tg, _ = frame(img, "telegram")
                    out["ok"].append({"file": name, "licence": p.licence, "credit": p.credit, "x": list(x.size), "telegram": list(tg.size),
                                      "aesthetic": aesthetic(img)})
                    logger.info("photography self-test: %s OK %s | %s | X %sx%s, Telegram %sx%s", key, name, p.credit, *x.size, *tg.size)
                else:
                    out["refused"].append({"file": name, "why": why})
                    logger.warning("photography self-test: %s REFUSED %s: %s", key, name, why)
        missing = len(subject.files) - len(out["ok"]) - len(out["refused"])
        logger.log(logging.INFO if out["ok"] and not out["refused"] else logging.WARNING, "photography self-test: %s %d of %d library photos usable%s",
                   key, len(out["ok"]), len(subject.files), f" ({missing} gone or refused on licence)" if missing else "")
    except Exception as e:  # noqa: BLE001 - a self-test never stops anything
        logger.warning("photography self-test: %s failed (%s)", key, type(e).__name__)
        out["error"] = type(e).__name__
    return out


def frame(img: Image.Image, platform: str) -> tuple[Image.Image, tuple[float, float, float, float]]:
    """The photograph cropped (never stretched) to the platform's frame, keeping its subject."""
    from app.newsdesk import photos

    size = FRAMES["x" if platform == "x" else "telegram"]
    out, box = photos.smart_crop(img, size)
    return photos.mild_tone(out), box


__all__ = ["CAPTIONS", "FRAMES", "LIBRARY", "PAIRS", "PEOPLE", "Subject", "people_of", "aesthetic", "frame", "library_photo", "rotation", "subject_for_symbol", "subject_of"]
