import csv
import io
import json
import re
import unicodedata
from pathlib import Path
from datetime import date, datetime
import requests

ROOT = Path(__file__).resolve().parent
TODAY = date.today()


def norm(value):
    value = str(value or "").lower().strip()
    value = "".join(
        c for c in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(c)
    )
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value)
    return value


def parse_number(value):
    s = str(value or "").strip().replace(" ", "")
    if not s:
        return None
    try:
        if "," in s and "." in s:
            # 1.099,99
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        return float(s)
    except ValueError:
        return None


def fetch(src):
    for url in (src.get("url"), src.get("fallback")):
        if not url:
            continue
        try:
            print(f"Downloading {src['name']}: {url}")
            r = requests.get(
                url,
                timeout=60,
                headers={
                    "User-Agent": "Mozilla/5.0 PriceTracker/3.0",
                    "Accept": "text/csv,text/plain,*/*",
                },
            )
            r.raise_for_status()
            if len(r.content) < 100:
                raise ValueError("Downloaded file is suspiciously small.")
            return r.content
        except Exception as exc:
            print(f"{src['name']} failed: {exc}")
    return None


def parse_csv(content):
    text = content.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=";,\t")
        delimiter = dialect.delimiter
    except Exception:
        delimiter = ";"
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


def get_field(row, wanted):
    nw = norm(wanted)
    for key, value in row.items():
        if norm(key) == nw:
            return value
    return ""


def product_name(row):
    return get_field(row, "Naziv proizvoda")


def category_name(row):
    return get_field(row, "NAZIV KATEGORIJE")


def format_name(row):
    return get_field(row, "Naziv trgovca - formata") or get_field(
        row, "Naziv trgovca – formata"
    )


def price_regular(row):
    return parse_number(get_field(row, "Redovna cena"))


def price_unit(row):
    return parse_number(get_field(row, "Cena po jedinici mere"))


def price_sale(row):
    return parse_number(get_field(row, "Snižena cena"))


def parse_date(value):
    value = str(value or "").strip()
    for fmt in ("%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def sale_is_active(row):
    sale = price_sale(row)
    if sale is None:
        return False

    start = parse_date(get_field(row, "Datum početka sniženja"))
    end = parse_date(get_field(row, "Datum kraja sniženja"))

    if start and TODAY < start:
        return False
    if end and TODAY > end:
        return False
    return True


def effective_pack_price(row):
    if sale_is_active(row):
        return price_sale(row)
    return price_regular(row)


def is_current_price_list(row):
    value = norm(get_field(row, "VRSTA_CENOVNIKA"))
    return not value or "vazeci_cenovnik" in value


def source_row_allowed(src, row):
    # Delhaize fajl sadrži Maxi, Mega Maxi, Shop&Go i online format.
    # Za sada pratimo samo standardni "Maxi", dok ne povežemo konkretan
    # objekat u Borči.
    if src.get("id") == "maxi":
        return norm(format_name(row)) == "maxi"
    return True


def tokens(text):
    return re.findall(r"[a-z0-9]+", norm(text))


def term_matches(text, term):
    """
    'vrat'   -> cela reč
    'svinj*' -> bilo koja reč koja počinje sa 'svinj'
    """
    ts = tokens(text)
    t = norm(term)

    if t.endswith("*"):
        prefix = t[:-1]
        return any(tok.startswith(prefix) for tok in ts)

    phrase_tokens = tokens(t)
    if len(phrase_tokens) == 1:
        return phrase_tokens[0] in ts

    return " ".join(phrase_tokens) in " ".join(ts)


def matches_product(row, product):
    name = product_name(row)
    category = category_name(row)

    if not name:
        return False

    allowed_categories = product.get("categories_any", [])
    if allowed_categories:
        ncat = norm(category)
        if not any(norm(c) in ncat for c in allowed_categories):
            return False

    for group in product.get("include_groups", []):
        if not any(term_matches(name, term) for term in group):
            return False

    for term in product.get("exclude", []):
        if term_matches(name, term):
            return False

    pack = parse_package(product, name)

    max_kg = product.get("max_package_kg")
    if max_kg is not None and pack.get("kg") is not None:
        if pack["kg"] > float(max_kg):
            return False

    return True


def parse_package(product, name):
    """
    Vraća poznatu veličinu fiksnog pakovanja.
    'ca', 'cca', 'rf', 'rinfuz' tretiramo kao promenljivu masu.
    """
    n = norm(name)

    variable_weight = any(
        marker in n
        for marker in (" cca ", " ca ", " rf", "rinfuz")
    )

    result = {
        "kg": None,
        "l": None,
        "count": None,
        "variable_weight": variable_weight,
    }

    # jaja: M6/1, 10/1, 6kom...
    if product.get("count_from_name"):
        m = re.search(r"(?<!\d)(\d{1,2})\s*kom\b", n)
        if not m:
            m = re.search(r"(?<!\d)(\d{1,2})\s*/\s*1\b", n)
        if m:
            result["count"] = int(m.group(1))

    if variable_weight:
        return result

    # kg / g
    kg_matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*kg\b", n)
    g_matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*g\b", n)

    if kg_matches:
        result["kg"] = float(kg_matches[-1].replace(",", "."))
    elif g_matches:
        result["kg"] = float(g_matches[-1].replace(",", ".")) / 1000.0
    elif product.get("slash_one_is_kg"):
        # npr. BRASNO 25/1 T-500
        m = re.search(r"(?<!\d)(\d{1,2})\s*/\s*1\b", n)
        if m:
            result["kg"] = float(m.group(1))

    # l / ml
    l_matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*l\b", n)
    ml_matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*ml\b", n)

    if l_matches:
        result["l"] = float(l_matches[-1].replace(",", "."))
    elif ml_matches:
        result["l"] = float(ml_matches[-1].replace(",", ".")) / 1000.0

    return result


def comparable_price(row, product):
    target_unit = product["unit"]
    pack_price = effective_pack_price(row)
    official_unit_price = price_unit(row)
    pack = parse_package(product, product_name(row))

    if pack_price is None:
        return None, pack

    if target_unit == "RSD/kom":
        if product.get("count_from_name") and pack.get("count"):
            return pack_price / pack["count"], pack
        return pack_price, pack

    if target_unit == "RSD/kg":
        if pack.get("variable_weight"):
            # Kod RF / rinfuz / cca artikala cena je uglavnom već po kg.
            if official_unit_price is not None and official_unit_price > 0:
                return official_unit_price, pack
            return pack_price, pack

        if pack.get("kg") and pack["kg"] > 0:
            return pack_price / pack["kg"], pack

        if official_unit_price is not None and official_unit_price > 0:
            return official_unit_price, pack

        return pack_price, pack

    if target_unit == "RSD/l":
        if pack.get("l") and pack["l"] > 0:
            return pack_price / pack["l"], pack

        if official_unit_price is not None and official_unit_price > 0:
            return official_unit_price, pack

        return pack_price, pack

    return pack_price, pack


def status_for(price, target):
    if price <= target:
        return "KUPI"
    if price <= target * 1.10:
        return "BLIZU"
    return "CEKAJ"


def main():
    products = json.loads(
        (ROOT / "products.json").read_text(encoding="utf-8")
    )["products"]

    sources = json.loads(
        (ROOT / "sources.json").read_text(encoding="utf-8")
    )["sources"]

    results = []

    for src in sources:
        if not src.get("enabled"):
            continue

        content = fetch(src)
        if not content:
            continue

        try:
            rows = parse_csv(content)
        except Exception as exc:
            print(f"{src['name']}: CSV parse error: {exc}")
            continue

        rows = [
            row for row in rows
            if is_current_price_list(row)
            and source_row_allowed(src, row)
        ]

        print(f"{src['name']}: {len(rows)} usable current rows")

        for product in products:
            candidates = []

            for row in rows:
                if not matches_product(row, product):
                    continue

                price, pack = comparable_price(row, product)

                if price is None or price <= 0:
                    continue

                # zaštita od očigledno besmislenih rezultata
                if product["unit"] == "RSD/kg" and price < 5:
                    continue
                if product["unit"] == "RSD/l" and price < 5:
                    continue

                candidates.append((price, row, pack))

            if not candidates:
                print(f"{src['name']} | {product['name']}: no valid match")
                continue

            price, row, pack = min(candidates, key=lambda x: x[0])

            result = {
                "date": str(TODAY),
                "store": src["name"],
                "product_id": product["id"],
                "product": product["name"],
                "matched_product": product_name(row),
                "category": category_name(row),
                "store_format": format_name(row),
                "price": round(price, 2),
                "regular_price": price_regular(row),
                "sale_price": price_sale(row) if sale_is_active(row) else None,
                "target": product["target_price"],
                "unit": product["unit"],
                "status": status_for(price, product["target_price"]),
                "package": pack,
                "barcode": get_field(row, "Barkod proizvoda"),
                "price_list_date": get_field(row, "Datum cenovnika"),
            }

            results.append(result)

            print(
                f"{src['name']} | {product['name']} -> "
                f"{result['matched_product']} -> "
                f"{price:.2f} {product['unit']} -> "
                f"{result['status']}"
            )

    order = {"KUPI": 0, "BLIZU": 1, "CEKAJ": 2}
    results.sort(
        key=lambda x: (
            order.get(x["status"], 9),
            x["product"],
            x["price"],
        )
    )

    docs = ROOT / "docs"
    history = ROOT / "data" / "history"
    docs.mkdir(exist_ok=True)
    history.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(results, ensure_ascii=False, indent=2)

    (docs / "results.json").write_text(payload, encoding="utf-8")
    (history / f"{TODAY}.json").write_text(payload, encoding="utf-8")

    print("=" * 60)
    print(f"Saved {len(results)} valid matches.")
    print("=" * 60)


if __name__ == "__main__":
    main()
