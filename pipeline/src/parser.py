from __future__ import annotations

import re
from bs4 import BeautifulSoup

from src.schemas import (
    EstateMasterRecord,
    ExtractionConfidence,
    FlavorCategory,
    ParsedOffering,
    RawProductSnapshot,
)

# Canonical region patterns -> normalized display name
REGION_PATTERNS: list[tuple[str, str]] = [
    (r"\bchikk?amagalur(?:u)?\b", "Chikmagalur"),
    (r"\bbaba\s*budan\s*giri\b|\bbababudangiri\b", "Bababudangiri"),
    (r"\bbiligirirangana\s*hills?\b|\bbr\s*hills?\b|\bb\.r\.\s*hills?\b", "Biligirirangana Hills"),
    (r"\bcoorg\b|\bkodagu\b", "Coorg"),
    (r"\bsakleshpur(?:a)?\b|\bmanjarabad\b", "Sakleshpur"),
    (r"\bhassan\b", "Hassan"),
    (r"\banamalais?\b|\bannamalais?\b|\bvalparai\b", "Anamalais"),
    (r"\bshevaroy\s*hills?\b|\byercaud\b", "Shevaroy Hills"),
    (r"\bpulney\s*hills?\b|\bpalani\s*hills?\b|\bkodaikanal\b", "Pulney Hills"),
    (r"\bnilgiris?\b|\bkotagiri\b|\boony\b|\booty\b|\bcoonoor\b", "Nilgiris"),
    (r"\bwayanad\b", "Wayanad"),
    (r"\baraku(?:\s*valley)?\b", "Araku Valley"),
    (r"\bkoraput\b", "Koraput"),
    (r"\bmeghalaya\b|\bkhasi\s*hills?\b|\bgaro\s*hills?\b", "Meghalaya"),
    (r"\bmizoram\b", "Mizoram"),
    (r"\btripura\b", "Tripura"),
    (r"\bnagaland\b", "Nagaland"),
]

CANONICAL_REGION_ALIASES: dict[str, str] = {
    "chikmagalur": "chikmagalur",
    "chikkamagaluru": "chikmagalur",
    "bababudangiri": "bababudangiri",
    "baba budangiri": "bababudangiri",
    "biligirirangana hills": "biligirirangana-hills",
    "br hills": "biligirirangana-hills",
    "coorg": "coorg",
    "kodagu": "coorg",
    "sakleshpur": "sakleshpur",
    "manjarabad": "sakleshpur",
    "hassan": "hassan",
    "anamalais": "anamalais",
    "valparai": "anamalais",
    "shevaroy hills": "shevaroy-hills",
    "yercaud": "shevaroy-hills",
    "pulney hills": "pulney-hills",
    "kodaikanal": "pulney-hills",
    "nilgiris": "nilgiris",
    "wayanad": "wayanad",
    "araku valley": "araku-valley",
    "araku": "araku-valley",
    "koraput": "koraput",
    "meghalaya": "meghalaya",
    "mizoram": "mizoram",
    "tripura": "tripura",
    "nagaland": "nagaland",
}

# Varietal regex -> canonical name (ordered most-specific first)
VARIETAL_PATTERNS: list[tuple[str, str]] = [
    (r"\b(?:sln|selection|sel|s)\.?\s*795\b|\bs795\b", "SLN 795"),
    (r"\b(?:sln|selection|sel|s)\.?\s*5\s*b\b|\bs5b\b", "SLN 5B"),
    (r"\b(?:sln|selection|sel|s)\.?\s*6\b|\bs6\b", "SLN 6"),
    (r"\b(?:sln|selection|sel|s)\.?\s*9\b|\bs9\b", "SLN 9"),
    (r"\bchandr[ai]giri\b", "Chandragiri"),
    (r"\bcauvery\b", "Cauvery"),
    (r"\bcatimor\b", "Catimor"),
    (r"\bcaturra\b", "Caturra"),
    (r"\byellow\s+bourbon\b", "Yellow Bourbon"),
    (r"\bred\s+bourbon\b", "Red Bourbon"),
    (r"\bbourbon\b", "Bourbon"),
    (r"\btypica\b", "Typica"),
    (r"\bgei?sha\b", "Gesha"),
    (r"\bkents?\b", "Kent"),
    (r"\bexcelsa\b", "Excelsa"),
    (r"\bliberica\b", "Liberica"),
    (r"\bc\s*x\s*r\b|\bcongensis\s*x\s*robusta\b", "CxR"),
]

CANONICAL_VARIETAL_ALIASES: dict[str, str] = {
    "sln 795": "sln-795",
    "selection 795": "sln-795",
    "s795": "sln-795",
    "s.795": "sln-795",
    "sln 9": "sln-9",
    "selection 9": "sln-9",
    "sln 5b": "sln-5b",
    "selection 5b": "sln-5b",
    "sln 6": "sln-6",
    "selection 6": "sln-6",
    "chandragiri": "chandragiri",
    "chandrigiri": "chandragiri",
    "cauvery": "cauvery",
    "catimor": "catimor",
    "caturra": "caturra",
    "yellow bourbon": "yellow-bourbon",
    "red bourbon": "red-bourbon",
    "bourbon": "bourbon",
    "typica": "typica",
    "gesha": "gesha",
    "geisha": "gesha",
    "kent": "kent",
    "excelsa": "excelsa",
    "liberica": "liberica",
    "cxr": "cxr",
}

# Process regex -> canonical process (ordered most-specific first)
PROCESS_PATTERNS: list[tuple[str, str]] = [
    (r"\bcarbonic\s+maceration\b", "Carbonic Maceration"),
    (r"\bcarbonic\s+natural\b|\bcm\s+natural\b", "Carbonic Natural"),
    (r"\bthermal\s+shock\b", "Thermal Shock"),
    (r"\bkoji(?:\s+fermented)?\b", "Koji Fermented"),
    (r"\byeast[\s-]+(?:inoculated|fermented)\b", "Yeast Inoculated"),
    (r"\bculture(?:d)?\s+natural\b", "Culture Natural"),
    (r"\bbarrel[\s-]+aged\b|\bwhiskey\s+barrel\b|\brum\s+barrel\b", "Barrel Aged"),
    (r"\banaerobic\s+(?:sun[\s-]*dried\s+)?natural\b|\b\d+[\s-]*h(?:ou)?r(?:s)?\s+anaerobic\s+natural\b", "Anaerobic Natural"),
    (r"\banaerobic\s+washed\b", "Anaerobic Washed"),
    (r"\bblack\s+honey\b", "Black Honey"),
    (r"\bred\s+honey\b", "Red Honey"),
    (r"\byellow\s+honey\b|\bgolden\s+honey\b", "Yellow Honey"),
    (r"\bmonsoon(?:ed)?(?:\s+malabar)?\b", "Monsooned"),
    (r"\bhoney(?:\s+process(?:ed)?|\s+sun[\s-]*dried)?\b|\bpulped\s+natural\b|\bpulped\s+sun[\s-]*dried\b", "Honey"),
    (r"\b(?:fully\s+|double\s+)?washed\b|\bwet[\s-]+process(?:ed)?\b", "Washed"),
    (r"\b(?:sun[\s-]*dried\s+|dry\s+)?natural\b|\bdry[\s-]+process(?:ed)?\b", "Natural"),
]

CANONICAL_PROCESS_ALIASES: dict[str, str] = {
    "washed": "washed",
    "fully washed": "washed",
    "natural": "natural",
    "sun dried natural": "natural",
    "honey": "honey",
    "black honey": "black-honey",
    "red honey": "red-honey",
    "yellow honey": "yellow-honey",
    "anaerobic natural": "anaerobic-natural",
    "anaerobic washed": "anaerobic-washed",
    "carbonic natural": "carbonic-natural",
    "carbonic maceration": "carbonic-maceration",
    "thermal shock": "thermal-shock",
    "yeast inoculated": "yeast-inoculated",
    "culture natural": "culture-natural",
    "koji fermented": "koji-fermented",
    "barrel aged": "barrel-aged",
    "monsooned": "monsooned",
}

ROAST_PATTERNS: list[tuple[str, str]] = [
    (r"\bmedium[\s/-]+dark\b|\bmed[\s/-]+dark\b", "Medium-Dark"),
    (r"\blight[\s/-]+medium\b|\blight[\s/-]+med\b|\bmedium[\s/-]+light\b", "Light-Medium"),
    (r"\bomni(?:[\s-]+roast)?\b", "Omni"),
    (r"\bultra[\s-]*light\b|\bfilter\s+light\b|\blight(?:\s+roast)?\b", "Light"),
    (r"\bdark(?:\s+roast)?\b|\bfrench\s+roast\b|\bespresso\s+dark\b", "Dark"),
    (r"\bmedium(?:\s+roast)?\b", "Medium"),
]

# Exhaustive Flavor Compass keyword dictionary mapped strictly to the 8 Tier-1 CHECK categories
FLAVOR_COMPASS_KEYWORDS: dict[FlavorCategory, tuple[str, ...]] = {
    "Fruity": (
        "apple", "green apple", "red apple", "apricot", "banana", "berry", "berries",
        "mixed berry", "blackberry", "blackcurrant", "black currant", "redcurrant",
        "blueberry", "blood orange", "cantaloupe", "cherry", "black cherry", "tart cherry",
        "citrus", "clementine", "cranberry", "currant", "date", "dates", "dragonfruit",
        "dried fruit", "fig", "figs", "fruit", "fruity", "grape", "red grape", "white grape",
        "grapefruit", "guava", "jackfruit", "jam", "jammy", "berry jam", "kiwi", "kumquat",
        "lemon", "lemonade", "lime", "sweet lime", "mosambi", "lychee", "mandarin", "mango",
        "ripe mango", "melon", "mulberry", "nectarine", "orange", "orange zest", "papaya",
        "passionfruit", "passion fruit", "peach", "ripe peach", "white peach", "pear",
        "persimmon", "pineapple", "plum", "black plum", "pomegranate", "pomelo", "prune",
        "raisin", "raspberry", "rhubarb", "stone fruit", "strawberry", "tamarind",
        "tangerine", "tropical", "tropical fruit", "watermelon", "yuzu", "zest", "kokum",
        "jamun", "amla", "starfruit", "sapota", "chikoo", "custard apple", "marmalade",
    ),
    "Floral": (
        "blossom", "chamomile", "elderflower", "floral", "flower", "frangipani", "geranium",
        "hibiscus", "honeysuckle", "jasmine", "lavender", "lilac", "lily", "lotus",
        "magnolia", "marigold", "neroli", "orange blossom", "osmanthus", "perfume",
        "perfumed", "rose", "rosewater", "rose water", "rosehip", "violet", "white floral",
        "white flower", "ylang", "bergamot", "earl grey", "mogra",
    ),
    "Sweet & Sugary": (
        "agave", "brown sugar", "butterscotch", "candy", "cane sugar", "caramel",
        "salted caramel", "cotton candy", "cream", "creamy", "creme brulee", "custard",
        "demerara", "fudge", "ghee", "golden syrup", "honey", "wildflower honey", "icing",
        "jaggery", "gur", "maple", "maple syrup", "marshmallow", "marzipan", "milk",
        "condensed milk", "molasses", "muscovado", "nougat", "panela", "piloncillo",
        "praline", "raw sugar", "sugar", "sugarcane", "sweet", "syrup", "syrupy", "toffee",
        "treacle", "vanilla", "vanilla bean", "white chocolate", "butter", "buttery",
        "shortbread", "biscuit", "graham cracker", "dulce de leche", "kheer", "malai",
        "mithai", "rabri", "mysore pak",
    ),
    "Nutty & Cocoa": (
        "almond", "roasted almond", "toasted almond", "brazil nut", "cacao", "cacao nib",
        "cacao nibs", "cashew", "chestnut", "chocolate", "dark chocolate", "milk chocolate",
        "baking chocolate", "cocoa", "cocoa nibs", "cocoa powder", "coconut", "gianduja",
        "hazelnut", "macadamia", "mocha", "nut", "nutella", "nutty", "peanut",
        "peanut butter", "pecan", "pine nut", "pistachio", "praline", "walnut", "brownie",
        "truffle", "ganache", "hot chocolate",
    ),
    "Spices": (
        "allspice", "anise", "star anise", "baking spice", "black pepper", "cardamom",
        "green cardamom", "chai", "masala chai", "cinnamon", "clove", "coriander", "cumin",
        "fennel", "fenugreek", "ginger", "gingerbread", "licorice", "mace", "nutmeg",
        "pepper", "peppercorn", "pink pepper", "saffron", "spice", "spiced", "spicy",
        "sweet spice", "turmeric", "warm spice", "cinnamon bark",
    ),
    "Roasted & Toasty": (
        "ashy", "burnt sugar", "cedar", "char", "charred", "cigar", "dark roast", "earthy",
        "espresso", "leather", "malt", "malty", "oak", "oaky", "pipe tobacco", "roast",
        "roasted", "roasty", "sandalwood", "smoke", "smoky", "toast", "toasted", "toasty",
        "tobacco", "wood", "woody", "walnut shell", "cereal", "roasted barley", "roasted grain",
    ),
    "Green & Vegetative": (
        "basil", "bay leaf", "black tea", "botanical", "cucumber", "eucalyptus", "fresh",
        "grass", "grassy", "green", "green tea", "matcha", "oolong", "white tea", "hay",
        "herb", "herbal", "herbaceous", "lemongrass", "mint", "minty", "olive", "parsley",
        "pea", "pine", "rosemary", "sage", "savory", "straw", "tea", "tea-like", "thyme",
        "tulsi", "vegetal", "verbena", "betel", "curry leaf",
    ),
    "Fermented & Boozy": (
        "alcohol", "amaretto", "beer", "boozy", "bourbon", "brandy", "champagne", "cider",
        "cognac", "ferment", "fermented", "funk", "funky", "gin", "kombucha", "liqueur",
        "liquor", "mead", "merlot", "pinot noir", "port", "port wine", "prosecco",
        "red wine", "rose wine", "rum", "rum raisin", "sake", "sangria", "sherry", "sour",
        "sparkling wine", "spirits", "tequila", "toddy", "vermouth", "vinegar", "whiskey",
        "whisky", "white wine", "wine", "winey", "vinous", "yeast", "yeasty", "yogurt",
    ),
}

# Field label mapping for HTML spec tables & key-value lines
FIELD_LABEL_MAP: dict[str, str] = {
    "estate": "estate",
    "farm": "estate",
    "producer": "producer",
    "grower": "producer",
    "origin": "origin",
    "region": "region",
    "location": "region",
    "elevation": "altitude",
    "altitude": "altitude",
    "varietal": "varietals",
    "varietals": "varietals",
    "variety": "varietals",
    "varieties": "varietals",
    "cultivar": "varietals",
    "varietal & altitude": "varietal_altitude",
    "varietal and altitude": "varietal_altitude",
    "variety & altitude": "varietal_altitude",
    "process": "process",
    "processing": "process",
    "processing method": "process",
    "fermentation": "process",
    "roast": "roast",
    "roast level": "roast",
    "roast profile": "roast",
    "roast type": "roast",
    "tasting notes": "tasting_notes",
    "flavor notes": "tasting_notes",
    "flavour notes": "tasting_notes",
    "cup notes": "tasting_notes",
    "cup profile": "tasting_notes",
    "sensory evaluation": "tasting_notes",
    "sensory notes": "tasting_notes",
    "notes": "tasting_notes",
    "tastes like": "tasting_notes",
}

NOISE_NOTE_PHRASES = {
    "acidity", "body", "balance", "balanced", "finish", "aftertaste", "mouthfeel",
    "sweetness", "clean", "bright", "crisp", "smooth", "juicy", "velvety", "round",
    "complex", "structured", "medium body", "full body", "light body", "high acidity",
    "low acidity", "medium acidity", "lingering finish", "clean finish", "none", "n/a",
}


def extract_structured_and_prose(body_html: str) -> tuple[dict[str, list[str]], str]:
    """
    Parse HTML using BeautifulSoup to extract:
      1. Spec table <tr> key-value pairs and inline Key: Value / Key - Value / Key | Value lines.
      2. Clean line-broken prose text.
    """
    if not body_html:
        return {}, ""

    soup = BeautifulSoup(body_html, "html.parser")
    fields: dict[str, list[str]] = {}

    def add_field(raw_key: str, raw_val: str) -> None:
        norm_key = re.sub(r"[\s:–—\-|]+$", "", raw_key.strip().lower())
        norm_key = re.sub(r"\s+", " ", norm_key)
        val = re.sub(r"\s+", " ", raw_val.strip())
        if not norm_key or not val:
            return
        canonical_key = FIELD_LABEL_MAP.get(norm_key)
        if canonical_key:
            fields.setdefault(canonical_key, []).append(val)

    # 1. Extract HTML table rows (<tr><td>Key</td><td>Value</td></tr>)
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                key_text = cells[0].get_text(" ", strip=True)
                val_text = " ".join(c.get_text(" ", strip=True) for c in cells[1:])
                add_field(key_text, val_text)

    # 2. Extract definition lists (<dt>/<dd>)
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            add_field(dt.get_text(" ", strip=True), dd.get_text(" ", strip=True))

    # 3. Extract clean line-broken prose
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"]):
        tag.insert_before("\n")
        tag.insert_after("\n")

    raw_text = soup.get_text(" ", strip=False)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw_text.splitlines()]
    lines = [line for line in lines if line]
    prose = "\n".join(lines)

    # 4. Scan key-value lines supporting :, -, –, —, |
    label_alternation = "|".join(
        re.escape(k) for k in sorted(FIELD_LABEL_MAP.keys(), key=len, reverse=True)
    )
    kv_pattern = re.compile(
        rf"^\s*({label_alternation})\s*(?::|[-–—|])\s*(.+?)\s*$",
        re.IGNORECASE,
    )
    for line in lines:
        # Also split pipe-separated inline segments like "Region: Chikmagalur | Process: Washed"
        segments = [seg.strip() for seg in line.split("|")] if line.count("|") >= 1 and ":" in line else [line]
        for seg in segments:
            match = kv_pattern.match(seg)
            if match:
                add_field(match.group(1), match.group(2))

    return fields, prose


def match_estates(
    text: str,
    estates_master: dict[str, EstateMasterRecord],
) -> list[tuple[str, str]]:
    """Return list of (estate_id, canonical_name) found in text, longest alias first."""
    if not text:
        return []
    lowered = text.lower()
    alias_entries: list[tuple[str, str, str]] = []
    for estate_id, record in estates_master.items():
        all_names = {record.name.lower(), *(a.lower() for a in record.aliases)}
        for alias in all_names:
            alias_entries.append((alias, estate_id, record.name))
    alias_entries.sort(key=lambda x: len(x[0]), reverse=True)

    found: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for alias, estate_id, canonical_name in alias_entries:
        if estate_id in seen_ids:
            continue
        pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"
        if re.search(pattern, lowered):
            seen_ids.add(estate_id)
            found.append((estate_id, canonical_name))
    return found


def extract_region(text: str) -> str | None:
    if not text:
        return None
    for pattern, canonical in REGION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return canonical
    return None


def extract_altitude(text: str) -> tuple[int | None, int | None]:
    if not text:
        return None, None

    # Normalize commas in numbers like 1,350 or 4,500
    cleaned = re.sub(r"(\d),(\d{3})", r"\1\2", text)

    # Range pattern with explicit unit: "1200-1450m", "1200 to 1450 masl", "4000 - 4500 feet"
    range_pat = re.compile(
        r"(\d{3,4})\s*(?:m|masl|meters?|metres?|ft|feet)?\s*(?:-|–|—|to)\s*(\d{3,4})\s*"
        r"(masl|m\.a\.s\.l\.?|meters?|metres?|m\b|feet|ft\b)",
        re.IGNORECASE,
    )
    single_pat = re.compile(
        r"(\d{3,4})\s*(masl|m\.a\.s\.l\.?|meters?|metres?|m\b|feet|ft\b)",
        re.IGNORECASE,
    )

    def to_masl(val: int, unit: str) -> int | None:
        u = unit.lower()
        masl = round(val * 0.3048) if ("ft" in u or "feet" in u) else val
        if 400 <= masl <= 2600:
            return masl
        return None

    m = range_pat.search(cleaned)
    if m:
        low = to_masl(int(m.group(1)), m.group(3))
        high = to_masl(int(m.group(2)), m.group(3))
        if low is not None and high is not None:
            return (min(low, high), max(low, high))
        if low is not None:
            return (low, low)
        if high is not None:
            return (high, high)

    m_single = single_pat.search(cleaned)
    if m_single:
        val = to_masl(int(m_single.group(1)), m_single.group(2))
        if val is not None:
            return (val, val)

    return None, None


def extract_varietals(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pattern, canonical in VARIETAL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            # Avoid adding generic Bourbon when Red/Yellow Bourbon already matched
            if canonical == "Bourbon" and ("Red Bourbon" in seen or "Yellow Bourbon" in seen):
                continue
            if canonical not in seen:
                seen.add(canonical)
                found.append(canonical)
    return found


def extract_process(text: str) -> str | None:
    if not text:
        return None
    for pattern, canonical in PROCESS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return canonical
    return None


def extract_roast_level(text: str) -> str | None:
    if not text:
        return None
    for pattern, canonical in ROAST_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return canonical
    return None


def split_tasting_notes(raw_text: str) -> list[str]:
    if not raw_text:
        return []
    # Strip trailing boilerplate sentences if a note line bleeds into prose
    cleaned = re.split(r"\.\s+[A-Z]", raw_text)[0]
    cleaned = re.sub(r"^(?:notes?\s+of|hints?\s+of|flavou?rs?\s+of)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" .;:•·-|")

    # Split on commas, semicolons, slashes, bullets, pipes, ampersands, and " and " / " with "
    parts = re.split(r"[,;/•·|&]+|\s+(?:and|with)\s+", cleaned, flags=re.IGNORECASE)

    notes: list[str] = []
    seen: set[str] = set()
    for part in parts:
        note = re.sub(r"\s+", " ", part).strip(" .;:•·-|\"'()").strip()
        if not note:
            continue
        # Reject overly long prose clauses
        if len(note) > 40 or len(note.split()) > 4:
            continue
        lowered = note.lower()
        if lowered in NOISE_NOTE_PHRASES:
            continue
        if lowered not in seen:
            seen.add(lowered)
            notes.append(note.title())
    return notes


def extract_tasting_notes_from_prose(prose: str) -> list[str]:
    if not prose:
        return []
    patterns = [
        r"(?:tasting\s+notes|flavou?r\s+notes|cup\s+notes|notes\s+of|tastes\s+like|sensory\s+(?:notes|profile))\s*(?::|is|are|include|-|–|—)?\s*([^\n\.]+)",
    ]
    for pat in patterns:
        match = re.search(pat, prose, re.IGNORECASE)
        if match:
            notes = split_tasting_notes(match.group(1))
            if notes:
                return notes
    return []


def classify_flavor_categories(tasting_notes: list[str]) -> list[FlavorCategory]:
    if not tasting_notes:
        return []
    matched: list[FlavorCategory] = []
    seen: set[FlavorCategory] = set()
    joined_notes = [n.lower() for n in tasting_notes]

    for note in joined_notes:
        for category, keywords in FLAVOR_COMPASS_KEYWORDS.items():
            if category in seen:
                continue
            for kw in keywords:
                pattern = r"(?<!\w)" + re.escape(kw) + r"(?!\w)"
                if re.search(pattern, note):
                    seen.add(category)
                    matched.append(category)
                    break
    return matched


def detect_is_blend(
    title: str,
    tags_text: str,
    prose: str,
    matched_estates: list[tuple[str, str]],
) -> bool:
    blend_pattern = r"\b(?:espresso\s+blend|house\s+blend|filter\s+blend|seasonal\s+blend|signature\s+blend|coffee\s+blend|blend)\b"
    if re.search(blend_pattern, title, re.IGNORECASE):
        return True
    if re.search(blend_pattern, tags_text, re.IGNORECASE):
        return True
    if len(matched_estates) >= 2:
        return True
    if re.search(r"\b(?:a\s+blend\s+of|blended\s+from|multi[\s-]*estate\s+blend)\b", prose, re.IGNORECASE):
        return True
    return False


def compute_confidence(
    estate_or_region: bool,
    process: str | None,
    varietals: list[str],
    tasting_notes: list[str],
) -> ExtractionConfidence:
    score = sum(
        [
            1 if estate_or_region else 0,
            1 if process else 0,
            1 if varietals else 0,
            1 if tasting_notes else 0,
        ]
    )
    if score >= 3:
        return "high"
    if score >= 1:
        return "medium"
    return "low"


def parse_raw_snapshot(
    snapshot: RawProductSnapshot,
    estates_master: dict[str, EstateMasterRecord],
) -> ParsedOffering:
    fields, prose = extract_structured_and_prose(snapshot.body_html)
    tags_text = " | ".join(snapshot.tags)

    def join_fields(*keys: str) -> str:
        vals: list[str] = []
        for k in keys:
            vals.extend(fields.get(k, []))
        return " | ".join(vals)

    spec_estate_text = join_fields("estate", "origin", "producer")
    spec_region_text = join_fields("region", "origin", "estate")
    spec_alt_text = join_fields("altitude", "varietal_altitude", "origin")
    spec_varietal_text = join_fields("varietals", "varietal_altitude")
    spec_process_text = join_fields("process")
    spec_roast_text = join_fields("roast")
    spec_notes_text = join_fields("tasting_notes")

    # Priority: Spec Table / KV Lines > Title > Tags > Prose
    estate_candidates = (
        match_estates(spec_estate_text, estates_master)
        or match_estates(snapshot.title, estates_master)
        or match_estates(tags_text, estates_master)
        or match_estates(prose, estates_master)
    )
    all_mentioned_estates = match_estates(f"{spec_estate_text}\n{snapshot.title}", estates_master)
    is_blend = detect_is_blend(snapshot.title, tags_text, prose, all_mentioned_estates)

    estate_id: str | None = None
    estate_name: str | None = None
    if not is_blend and estate_candidates:
        estate_id, estate_name = estate_candidates[0]

    region = (
        extract_region(spec_region_text)
        or extract_region(snapshot.title)
        or extract_region(tags_text)
        or extract_region(prose)
    )

    alt_min, alt_max = extract_altitude(spec_alt_text)
    if alt_min is None:
        alt_min, alt_max = extract_altitude(snapshot.title)
    if alt_min is None:
        alt_min, alt_max = extract_altitude(tags_text)
    if alt_min is None:
        alt_min, alt_max = extract_altitude(prose)

    varietals = (
        extract_varietals(spec_varietal_text)
        or extract_varietals(snapshot.title)
        or extract_varietals(tags_text)
        or extract_varietals(prose)
    )

    process = (
        extract_process(spec_process_text)
        or extract_process(snapshot.title)
        or extract_process(tags_text)
        or extract_process(prose)
    )

    roast_level = (
        extract_roast_level(spec_roast_text)
        or extract_roast_level(snapshot.title)
        or extract_roast_level(tags_text)
        or extract_roast_level(prose)
    )

    tasting_notes: list[str] = []
    if spec_notes_text:
        tasting_notes = split_tasting_notes(spec_notes_text)
    if not tasting_notes:
        tasting_notes = extract_tasting_notes_from_prose(prose)

    flavor_categories = classify_flavor_categories(tasting_notes)

    confidence = compute_confidence(
        estate_or_region=bool(estate_id or region),
        process=process,
        varietals=varietals,
        tasting_notes=tasting_notes,
    )

    return ParsedOffering(
        roaster_id=snapshot.roaster_id,
        estate_id=estate_id,
        estate_name=estate_name,
        source_product_id=snapshot.source_product_id,
        title=snapshot.title,
        source_url=snapshot.source_url,
        is_blend=is_blend,
        region=region,
        altitude_min_masl=alt_min,
        altitude_max_masl=alt_max,
        varietals=varietals,
        process=process,
        roast_level=roast_level,
        tasting_notes_raw=tasting_notes,
        flavor_categories=flavor_categories,
        in_stock=True,
        min_price_inr=None,
        price_250g_inr=None,
        extraction_confidence=confidence,
        content_hash=snapshot.content_hash,
    )