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
import dataclasses
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
    # owner, 2026-11-24: a broad subject (stock markets in general) is the picture only when the story names nothing more specific: a
    # person or any other subject it names, wherever in the headline, comes first
    fallback: bool = False

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
    # owner, 2026-11-23 (library expansion): every file below was read live on Commons and looked at; licences re-verified at every use.
    # OPEC comes before oil: a headline naming OPEC shows OPEC's own building, not a generic oil picture
    Subject("opec", "OPEC", r"\bopec\+?(?=\W|$)", (
        "File:Wien - OPEC-Zentrale, Portal.JPG",  # the canopy reading ORGANIZATION OF THE PETROLEUM EXPORTING COUNTRIES, 2021
        "File:Entrance OPEC Headquarter Vienna.JPG",
        "File:Wien - OPEC-Zentrale (a).JPG",
    )),
    # 2026-11-24 audit: "Malaysia Palm Oil Industry..." showed oil pumpjacks. Palm, olive or cooking oil is not crude.
    Subject("oil", "Oil", r"(?<!palm )(?<!olive )(?<!cooking )(?<!vegetable )(?<!edible )(?<!soybean )(?<!sunflower )(?<!coconut )"
                          r"(?<!essential )(?<!castor )(?<!fish )(?<!motor )(?<!snake )\b(oil|crude|brent|wti|opec\+?|diesel|gasoline)\b"
                          r"(?! paint\w*)", (
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
    # owner, 2026-11-23 (library expansion): institutions the desk names most, each photo showing the institution's own building or name
    Subject("meta", "Meta", r"\b(meta platforms|facebook|instagram|whatsapp)\b|(?-i:\bMeta(?:'s|’s)?\b(?!-))", (
        "File:Meta i Almedalen.jpeg",  # the large Meta lettering, 2022
    )),
    Subject("boe", "The Bank of England", r"\b(bank of england|threadneedle street)\b|(?-i:\bBoE\b|\bBOE\b)", (
        "File:Bank of England Building, London, UK - Diliff.jpg",
        "File:Bank of England Building, City of London (Southwest View - 01).jpg",
    )),
    Subject("boj", "The Bank of Japan", r"\bbank of japan\b|(?-i:\bBoJ\b|\bBOJ\b)", (
        "File:Bank of Japan 20190223.jpg",
        "File:Bank of Japan Head Office, Tokyo - Aug 29, 2011.jpg",
        "File:Bank of Japan Head Office,Chuo-city,Tokyo,Japan.jpg",
    )),
    Subject("treasury", "The US Treasury", r"\b(u\.?s\.? treasury department|treasury department|department of the treasury)\b", (
        "File:U.S. Treasury Building and Albert Gallatin Statue.jpg",
        "File:Treasury Building (1).jpg",
        "File:Us-treasury-building.jpg",
    )),
    Subject("sec", "The SEC", r"(?-i:\bSEC(?:'s|’s)?\b)|\bsecurities and exchange commission\b", (
        "File:U.S. Securities and Exchange Commission headquarters.JPG",
        "File:Facade of the U.S. Securities and Exchange Commission headquarters, Washington, D.C.jpg",
    )),
    # the US Congress only: "Senate" or "Congress" alone can be another country's
    Subject("capitol", "The US Capitol", r"\b(u\.?s\.? capitol|capitol hill|u\.?s\.? congress|u\.?s\.? senate|u\.?s\.? house of representatives|congressional)\b", (
        "File:The dome of the United States Capitol building.jpg",
        "File:US Capitol Building at night Jan 2006.jpg",
        "File:United States Capitol west side at night.jpg",
        "File:Capitol at Dusk 2.jpg",
    )),
    Subject("white_house", "The White House", r"\bwhite house\b", (
        "File:The White House June 2024.jpg",
        "File:North Façade White House.JPG",
        "File:White House 05.jpg",
    )),
    Subject("imf", "The IMF", r"(?-i:\bIMF(?:'s|’s)?\b)|\binternational monetary fund\b", (
        "File:11 International Monetary Fund IMF in Washington DC USA - Creative Commons CC-BY.jpg",  # the name on the wall
        "File:International Monetary Fund (IMF) (53844898894).jpg",
    )),
    Subject("world_bank", "The World Bank", r"\bworld bank\b", (
        "File:The World Bank Group (53844804648).jpg",
        "File:The World Bank Group.jpg",  # the name carved in stone
    )),
    Subject("stocks", "Wall Street", r"\b(s&p 500|dow jones|wall street|wall st\b|nyse|new york stock exchange|u\.s\. (?:tech |bank |chip )?stocks|"
                                     r"us (?:tech |bank |chip )?stocks|american stocks|dow(?! chemical))\b", (
        "File:Sign of the New York Stock Exchange, Broad Street.jpg",
        "File:New York Stock Exchange Facade.JPG",
        "File:NEW YORK STOCK EXCHANGE 20240521.jpg",
    )),
    # owner, 2026-11-24 (coverage pass over the week's 567 publish-worthy stories): the biggest gaps a licensed photograph can honestly
    # fill. Each file was read live on Commons (licence, author, description) and looked at before it went in; licences re-verified at use.
    # Anthropic (29 stories without a picture, its IPO week): there is no photograph on Commons that identifies Anthropic itself (the
    # office photos show no name), so its co-founder and CEO, captioned with where and when, as Binance shows its founder
    Subject("anthropic", "Anthropic", r"\banthropic(?:'s|’s)?\b", (
        "File:Dario Amodei at TechCrunch Disrupt 2023 01.jpg",
        "File:Dario Amodei at TechCrunch Disrupt 2023 07.jpg",
        "File:Dario Amodei at TechCrunch Disrupt 2023 08.jpg",
        "File:Dario Amodei at TechCrunch Disrupt 2023 04.jpg",
    )),
    Subject("openai", "OpenAI", r"\bopenai(?:'s|’s)?\b", (  # its CEO, as above (the OpenAI files on Commons are logos and screenshots)
        "File:Sam Altman November 2022.jpg",
    )),
    Subject("strategy", "Strategy", r"\b(microstrategy|mstr)\b", (  # Strategy (MSTR): its executive chairman
        "File:Michael Saylor (54559609943).jpg",
        "File:Michael Saylor (54345839472).jpg",
    )),
    # the strait itself from orbit (NASA, public domain): never a warship, never a burning tanker
    Subject("hormuz", "The Strait of Hormuz", r"\b(strait of hormuz|hormuz)\b", (
        "File:Strait of Hormuz (MODIS 2020-12-04).jpg",
        "File:ISS-46 Gulf of Oman with Strait of Hormuz at night.jpg",
        "File:Strait of Hormuz and Musandam Peninsula (MODIS 2018-12-10).jpg",
    )),
    Subject("tokyo_stocks", "The Tokyo Stock Exchange", r"\b(nikkei(?: 225)?|topix|tokyo stock exchange|tokyo stocks|japan(?:ese)? (?:stocks|shares|equities))\b", (
        "File:TSE Arrows 20240329.jpg",  # the trading floor's glass cylinder and ticker ring, 2024
        "File:Tokyo Stock Exchange (28174991346).jpg",
        "File:Tokyo Stock Exchange (28208458775).jpg",
    )),
    # stock markets in general ("Global stocks set for worst run", "Tech stock sell-off deepens in Asia"): no exchange, no country, so the
    # picture never says Wall Street about Seoul. Only when the story names nothing more specific (fallback).
    Subject("markets", "Stock markets", r"\b(stock[- ]markets?|(?:global|world|asian?|asia-pacific|european|emerging[- ]markets?|tech|technology|bank|chip|ai) "
                                        r"(?:stocks|shares|equities)|stocks (?:plunge|tumble|slide|slump|fall|fell|drop|sink|sank|slip|crash|rall|rebound|rise|rose|surge|soar)\w*|"
                                        r"equity markets?|markets? (?:rout|sell-?off|crash|plunge|tumble|slump)\w*|(?:tech|ai|stock|market|global) sell-?offs?|"
                                        r"bear market|stock futures)\b", (
        "File:Checking stock market prices (Unsplash).jpg",
    ), fallback=True),
)
# subjects whose photograph is a person (a company shown by its founder or chief): never on a story of violence, always captioned
PORTRAITS = frozenset({"binance", "anthropic", "openai", "strategy"})
# -------------------------------------------------------------------------------------------------- people (owner, 2026-11-19)
# "Trump/Xi/Zelensky story -> relevant real photograph of the people or event." Leaders and the market's most-named people, each file looked
# at before it went in (official White House photos are public domain; Kremlin, Web Summit and Commons photographers' files are CC BY/BY-SA/
# CC0, re-verified live). A pair is used when the story names both; otherwise the person named first. CAPTIONS says who is pictured, where
# and when, drawn with the credit: an archive photo never passes for today's meeting. Never in a story of violence (clean.picture).
# owner, 2026-11-23 (photo audit): the photographer as they ask to be credited, where Commons' Artist field is an uploader account or too
# long for the corner of the picture (it was cut to "Gage Skidmore from Surprise…"). Every name here is the author the file's record gives.
CREDITS: dict[str, str] = {
    "File:Michael Saylor (54559609943).jpg": "Gage Skidmore",  # "Please attribute to Gage Skidmore if used elsewhere."
    "File:Michael Saylor (54345839472).jpg": "Gage Skidmore",
    "File:Michael Saylor (54345839687).jpg": "Gage Skidmore",
    "File:11 International Monetary Fund IMF in Washington DC USA - Creative Commons CC-BY.jpg": "Marek Ślusarczyk (Tupungato)",
    "File:Official portrait of Treasury Secretary Scott Bessent.jpg": "US Department of the Treasury",
    # owner, 2026-11-24 (coverage pass)
    "File:Tokyo Stock Exchange (28174991346).jpg": "franck injapan",  # Flickr account "franck injapan from Tokyo, Japan"
    "File:Tokyo Stock Exchange (28208458775).jpg": "franck injapan",
    "File:Strait of Hormuz (MODIS 2020-12-04).jpg": "NASA GSFC (MODIS)",  # "MODIS Land Rapid Response Team, NASA GSFC"
    "File:Strait of Hormuz and Musandam Peninsula (MODIS 2018-12-10).jpg": "NASA GSFC (MODIS)",
    "File:Checking stock market prices (Unsplash).jpg": "William Iven",  # "William Iven firmbee" (name and Unsplash handle)
}

CAPTIONS: dict[str, str] = {
    # owner, 2026-11-23 (library expansion): who, where and when, so an archive photo never passes for today's event
    "File:Chairman Warsh answers reporters' questions at the FOMC press conference on June 17, 2026 (2).jpg": "Fed Chair Kevin Warsh at the FOMC press conference, June 2026",
    "File:Official portrait of Kevin M. Warsh.jpg": "Fed Chair Kevin Warsh, official portrait, 2026",
    "File:Visite de Christine Lagarde, Présidente de la Banque centrale européenne le Jeudi 20 avril 2023 à l’Ecole polytechnique (52834400095).jpg": "Christine Lagarde at the Ecole polytechnique, April 2023",
    "File:AC SO Karlspreisverleihung 2026 14. Mai Krönungssaal Christine Lagarde I.jpg": "Christine Lagarde in Aachen, May 2026",
    "File:P20250929DT-0078 President Donald Trump and Israeli Prime Minister Benjamin Netanyahu hold a joint press conference.jpg": "Benjamin Netanyahu at the White House, September 2025",
    "File:Recep Tayyip Erdoğan, M11 Arnavutköy-Halkalı etabının açılış töreninde 3.jpg": "Recep Tayyip Erdogan in Istanbul, June 2026",
    "File:Sam Altman November 2022.jpg": "Sam Altman, November 2022",
    "File:Dario Amodei at TechCrunch Disrupt 2023 01.jpg": "Dario Amodei at TechCrunch Disrupt, September 2023",
    "File:Dario Amodei at TechCrunch Disrupt 2023 04.jpg": "Dario Amodei at TechCrunch Disrupt, September 2023",
    "File:Michael Saylor (54559609943).jpg": "Michael Saylor at Bitcoin 2025, May 2025",
    "File:Michael Saylor (54345839472).jpg": "Michael Saylor at CPAC, February 2025",
    "File:Michael Saylor (54345839687).jpg": "Michael Saylor at CPAC, February 2025",
    "File:P20260528AM-0092 Treasury Secretary Scott Bessent holds a White House Press Briefing.jpg": "Scott Bessent at the White House, May 2026",
    "File:P20260528AM-0063 Treasury Secretary Scott Bessent holds a White House Press Briefing.jpg": "Scott Bessent at the White House, May 2026",
    "File:Official portrait of Treasury Secretary Scott Bessent.jpg": "Scott Bessent, official portrait, 2025",
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
    "File:2022 - Press Conferences BE6 1583 (52472585725).jpg": "Binance founder Changpeng Zhao at Web Summit, November 2022",
    # owner, 2026-11-24 (coverage pass)
    "File:Dario Amodei at TechCrunch Disrupt 2023 07.jpg": "Dario Amodei at TechCrunch Disrupt, September 2023",
    "File:Dario Amodei at TechCrunch Disrupt 2023 08.jpg": "Dario Amodei at TechCrunch Disrupt, September 2023",
    "File:China-Russia-U.S. Shenzhen Summit 2026.jpg": "Putin, Xi and Trump at the Shenzhen summit, November 2026",
    "File:Xi–Putin–Trump summit - P20261119DT-0901 (55662934489).jpg": "Putin, Xi and Trump at their Shenzhen summit talks, November 2026",
    "File:Vladimir Putin, Xi Jinping and Donald Trump at APEC China 2026.jpg": "Trump, Xi and Putin at APEC in China, November 2026",
    "File:TSE Arrows 20240329.jpg": "The Tokyo Stock Exchange trading floor, March 2024",
    "File:Tokyo Stock Exchange (28174991346).jpg": "The Tokyo Stock Exchange, March 2015",
    "File:Tokyo Stock Exchange (28208458775).jpg": "The Tokyo Stock Exchange, March 2015",
    "File:Strait of Hormuz (MODIS 2020-12-04).jpg": "The Strait of Hormuz from orbit, December 2020",
    "File:ISS-46 Gulf of Oman with Strait of Hormuz at night.jpg": "The Gulf of Oman and Strait of Hormuz from the ISS, January 2016",
    "File:Strait of Hormuz and Musandam Peninsula (MODIS 2018-12-10).jpg": "The Strait of Hormuz from orbit, December 2018",
    "File:Checking stock market prices (Unsplash).jpg": "Stock prices on a phone, 2015",
}

# 2026-11-24 audit: "Circle and Trump-linked crypto firm hit with court freeze order" showed Donald Trump; "Earnings trump forecasts" is a verb
TRUMP = (r"(?-i:\b(?:Trump|TRUMP)(?:'s|’s|'S|’S)?\b)(?!-(?:linked|backed|family|era|appointed|aligned|allied|style|branded|owned|affiliated|"
         r"connected|tied|related|endorsed|friendly|supporting)\b)")
XI = r"(?-i:\bXi(?: Jinping)?\b|\bXI JINPING\b)"
# the three leaders together (the Shenzhen summit, 2026-11-19): 16 stories that week named all three and got a Trump-Putin pair from Alaska
TRIOS: tuple[tuple[Subject, tuple[str, ...]], ...] = (
    (Subject("trump_xi_putin", "Donald Trump, Xi Jinping and Vladimir Putin", TRUMP, (
        "File:China-Russia-U.S. Shenzhen Summit 2026.jpg",  # White House, public domain
        "File:Vladimir Putin, Xi Jinping and Donald Trump at APEC China 2026.jpg",  # kremlin.ru, CC BY 4.0
        "File:Xi–Putin–Trump summit - P20261119DT-0901 (55662934489).jpg",  # White House (Daniel Torok), public domain
    )), (TRUMP, XI, r"\bputin(?:'s|’s)?\b")),
)
PAIRS: tuple[tuple[Subject, str, str], ...] = (  # (subject, first name pattern, second name pattern)
    (Subject("trump_xi", "Donald Trump and Xi Jinping", TRUMP, (
        "File:Donald Trump visit to China, May 2026 P20260514DT-1182.jpg",
        "File:President Donald Trump greets Chinese President Xi Jinping before a bilateral meeting at the Gimhae International Airport terminal (54890669438).jpg",
        "File:Donald Trump visit to China, May 2026T-0783.jpg",
        "File:P20260924DT-2338 (55563017265).jpg",
    )), TRUMP, XI),
    (Subject("trump_putin", "Donald Trump and Vladimir Putin", TRUMP, (
        "File:P20250815DT-1255 President Donald Trump meets with Russian President Vladimir Putin at Joint Base Elmendorf Richardson in Anchorage, Alaska.jpg",
    )), TRUMP, r"\bputin(?:'s|’s)?\b"),
    (Subject("trump_zelensky", "Donald Trump and Volodymyr Zelensky", TRUMP, (
        "File:President Donald Trump participates in a bilateral meeting with Ukrainian president Volodymyr Zelenskyy P20250625DT-0772.jpg",
        "File:Donald Trump hosts a meeting with Ukrainian President Volodymyr Zelenskyy in the Cabinet Room of the White House on October 17, 2025 - 1.jpg",
    )), TRUMP, r"\bzelensk\w*\b"),
    (Subject("trump_netanyahu", "Donald Trump and Benjamin Netanyahu", TRUMP, (
        "File:President Donald Trump walks with Israeli Prime Minister Benjamin Netanyahu at the Knesset in Jerusalem (54855555063).jpg",
        "File:President Donald Trump is greeted by Israeli Prime Minister Benjamin Netanyahu and others after disembarking Air Force One at Ben Gurion International Airport (54855297376).jpg",
    )), TRUMP, r"\bnetanyahu(?:'s|’s)?\b"),
    (Subject("xi_putin", "Xi Jinping and Vladimir Putin", XI, (
        "File:Xi Jinping and Vladimir Putin at the parade marking the 80th anniversary of the Great Victory at Great Patriotic War (2025).jpg",
    )), XI, r"\bputin(?:'s|’s)?\b"),
)
PEOPLE: tuple[Subject, ...] = (
    Subject("trump", "Donald Trump", TRUMP, (
        "File:P20251106AM-0645 President Donald Trump makes an announcement on lowering drug prices in the Oval Office.jpg",
        "File:President Donald Trump participates in a bilateral meeting with Chinese President Xi Jinping at the Gimhae International Airport terminal (54890669548).jpg",
    )),
    Subject("xi", "Xi Jinping", XI, (
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
    # owner, 2026-11-23 (library expansion): each person alone in the frame (a two-leader photo is not one person's picture)
    # 2026-11-24 framing check (both frames, live files): Warsh's tall official portrait cropped to his tie on X, and Lagarde at the Ecole
    # polytechnique to her back at the lectern; neither identifies the person, so both are out of rotation
    Subject("warsh", "Kevin Warsh", r"\bwarsh(?:'s|’s)?\b", (
        "File:Chairman Warsh answers reporters' questions at the FOMC press conference on June 17, 2026 (2).jpg",
    )),
    Subject("lagarde", "Christine Lagarde", r"\blagarde(?:'s|’s)?\b", (
        "File:AC SO Karlspreisverleihung 2026 14. Mai Krönungssaal Christine Lagarde I.jpg",
    )),
    Subject("netanyahu", "Benjamin Netanyahu", r"\bnetanyahu(?:'s|’s)?\b", (
        "File:P20250929DT-0078 President Donald Trump and Israeli Prime Minister Benjamin Netanyahu hold a joint press conference.jpg",
    )),
    Subject("erdogan", "Recep Tayyip Erdogan", r"\berdo[gğ]an(?:'s|’s)?\b", (
        "File:Recep Tayyip Erdoğan, M11 Arnavutköy-Halkalı etabının açılış töreninde 3.jpg",
    )),
    # 2026-11-24: "Sam Altman speaking at TED.jpg" is out: a tall full-length shot, both frames cropped it to his chair and shoes
    Subject("altman", "Sam Altman", r"\bsam altman\b", (
        "File:Sam Altman November 2022.jpg",
    )),
    Subject("amodei", "Dario Amodei", r"\b(dario )?amodei\b", (
        "File:Dario Amodei at TechCrunch Disrupt 2023 01.jpg",
        "File:Dario Amodei at TechCrunch Disrupt 2023 04.jpg",
    )),
    Subject("saylor", "Michael Saylor", r"\bsaylor(?:'s|’s)?\b", (
        "File:Michael Saylor (54559609943).jpg",
        "File:Michael Saylor (54345839472).jpg",
        "File:Michael Saylor (54345839687).jpg",
    )),
    Subject("bessent", "Scott Bessent", r"\bbessent(?:'s|’s)?\b", (
        "File:P20260528AM-0092 Treasury Secretary Scott Bessent holds a White House Press Briefing.jpg",
        "File:P20260528AM-0063 Treasury Secretary Scott Bessent holds a White House Press Briefing.jpg",
        "File:Official portrait of Treasury Secretary Scott Bessent.jpg",
    )),
    Subject("cz", "Changpeng Zhao", r"\bchangpeng zhao\b|\bzhao changpeng\b|(?-i:\bCZ\b)", (
        "File:2022 - Press Conferences BE6 1591 (52472274614).jpg",
        "File:2022 - Q&A PO1 4700 (52471974491).jpg",
        "File:2022 - Press Conferences BE6 1583 (52472585725).jpg",
    )),
)


def story_text(headline: str, title: str = "") -> str:
    """The words a picture is matched against. A headline in capitals ("TRUMP TO MEET XI, PUTIN") hides the case the matching relies on
    ("Xi" is a name, "XI" is also a team sheet; "Fed", "SEC", "Meta"), so the source's own title (same story, normal case) is read first
    and the capitals after it: whoever the story names first is the same person, and 2026-11-24's audit found 16 Trump-Xi-Putin stories
    that got a Trump-Putin photo because "XI" was invisible in the capitals."""
    h, t = (headline or "").strip(), (title or "").strip()
    if not t or t == h:
        return h or t
    if not h:
        return t
    shouted = not re.search(r"[a-z]", h) and re.search(r"[a-z]", t)
    return f"{t} {h}" if shouted else f"{h} {t}"


def _given_name_before(t: str, at: int) -> bool:
    """A different first name right before a surname ("Alice Huang", "Colin Huang"): another person of that name."""
    m = re.search(r"([A-Z][a-z]+|[A-Z]{2,})\s+$", t[:at])
    return bool(m) and m.group(1).lower() not in ("jensen", "ceo", "nvidia", "boss", "chief", "founder", "mr", "says", "as", "and", "by")


def people_of(text: str) -> tuple[Subject, int] | None:
    """(the people photograph a headline calls for, where in it they are first named), or None. All three summit leaders when the story
    names them; else a pair when both are named (the pair named earliest when there are several: "Trump tells Xi before trilateral with
    Putin" is Trump and Xi), else the person named first."""
    t = text or ""
    if re.search(r"\bXI\b", t) and re.search(r"\b(CHINA|CHINESE|BEIJING|JINPING|TRUMP|PUTIN|SUMMIT|SHENZHEN)\b", t):
        t = re.sub(r"\bXI\b", "Xi", t)  # the Chinese leader in a headline set in capitals (same length: positions are kept)
    for s, names in TRIOS:
        found = [re.search(n, t, re.I) for n in names]
        if all(found):
            # the three together only when the story lists them together ("Trump to meet Xi, Putin", "talks with Trump and Xi"); "Trump
            # tells Xi before trilateral with Putin" is the two who spoke (Telegram 197)
            ms = sorted((m for m in found if m), key=lambda m: m.start())
            gaps = [t[a.end():b.start()] for a, b in zip(ms, ms[1:])]
            if any(len(g) <= 25 and re.search(r",|\band\b|&", g, re.I) for g in gaps):
                return s, ms[0].start()
    pairs = []
    for s, a, b in PAIRS:
        ma, mb = re.search(a, t, re.I), re.search(b, t, re.I)
        if ma and mb:
            pairs.append((max(ma.start(), mb.start()), min(ma.start(), mb.start()), s))
    if pairs:
        _, first, s = min(pairs, key=lambda p: p[0])
        return s, first
    # "Huang" alone is Jensen Huang only beside Jensen or Nvidia (PDD's Colin Huang is not)
    solo = [(m.start(), s) for s in PEOPLE if (m := s.rx().search(t)) and (s.key != "jensen_huang" or (re.search(r"jensen|nvidia", t, re.I)
                                                                         and (m.group(0).lower().startswith("jensen") or not _given_name_before(t, m.start()))))]
    if solo:
        at, s = min(solo, key=lambda h: h[0])
        return s, at
    return None




BY_KEY = {s.key: s for s in (*LIBRARY, *PEOPLE, *(p[0] for p in PAIRS), *(t[0] for t in TRIOS))}
# a data post's or market move's symbol -> its subject
SYMBOLS = {"GC=F": "gold", "XAU": "gold", "GOLD": "gold", "PAXG": "gold", "SI=F": "silver", "SILVER": "silver", "BTC-USD": "bitcoin", "BTC": "bitcoin",
           "ETH-USD": "ether", "ETH": "ether", "CL=F": "oil", "BZ=F": "oil", "CL": "oil", "OIL": "oil", "^GSPC": "stocks", "^IXIC": "stocks", "US-STOCKS": "stocks",
           "^DJI": "stocks", "NG=F": "natgas", "HG=F": "copper", "ZW=F": "grains", "DOGE-USD": "dogecoin", "DOGE": "dogecoin", "META": "meta",
           "AMZN": "amazon", "GOOGL": "google", "GOOG": "google", "TSM": "tsmc", "JPM": "jpmorgan", "MSFT": "microsoft", "NVDA": "nvidia", "AAPL": "apple", "TSLA": "tesla",
           "MSTR": "strategy", "^N225": "tokyo_stocks"}

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
    if not hits:
        return None
    first = min(hits, key=lambda h: h[0])
    named = [h for h in hits if not h[1].fallback]
    if not named:
        return first[1]
    best = min(named, key=lambda h: h[0])
    # a broad subject only when nothing more specific is named, except that a market-wide story named first ("Asian stocks plunge as
    # Anthropic fails...") is not a portrait of the chief of a company it mentions
    return first[1] if first[1].fallback and best[1].key in PORTRAITS and first[0] < best[0] else best[1]


def symbol_key(symbol: str) -> str:
    """A symbol's library key: "BTC", "$BTC", "BTC-USD", and a derivatives contract on it ("LIQ:BTCUSD_PERP", "BTCUSD_270326", "ETHUSDT")."""
    s = str(symbol or "").upper().lstrip("$").split(":")[-1]
    if s in SYMBOLS:
        return SYMBOLS[s]
    m = re.match(r"^([A-Z]+?)(?:[-_/]?(?:USDT|USDC|USD|PERP|\d{6}))+$", s)
    return SYMBOLS.get(m.group(1), "") if m else ""


def subject_for_symbol(symbol: str, name: str = "") -> Subject | None:
    key = symbol_key(symbol)
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
                if (named := CREDITS.get(str(p.title))):
                    p = dataclasses.replace(p, creator=named)
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


def frame(img: Image.Image, platform: str) -> tuple[Image.Image, tuple[float, float, float, float]]:
    """The photograph cropped (never stretched) to the platform's frame, keeping its subject."""
    from app.newsdesk import photos

    size = FRAMES["x" if platform == "x" else "telegram"]
    out, box = photos.smart_crop(img, size)
    return photos.mild_tone(out), box


__all__ = ["CAPTIONS", "FRAMES", "LIBRARY", "PAIRS", "PEOPLE", "PORTRAITS", "TRIOS", "Subject", "people_of", "story_text", "symbol_key", "aesthetic", "frame", "library_photo", "rotation", "subject_for_symbol", "subject_of"]
