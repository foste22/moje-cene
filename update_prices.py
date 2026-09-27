import csv
import io
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("Europe/Belgrade")
NOW = datetime.now(TZ)
TODAY = NOW.date()
PARSER_VERSION = "final-1.0"


def norm(value):
    value = str(value or "").lower().strip().replace("đ", "dj")
    value = "".join(
        c for c in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(c)
    )
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value)
    return value


def tokens(text):
    return re.findall(r"[a-z0-9]+", norm(text))


def parse_number(value):
    s = str(value or "").strip().replace(" ", "")
    if not s:
        return None
    try:
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        return float(s)
    except ValueError:
        return None


def parse_date(value):
    value = str(value or "").strip()
    for fmt in ("%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def field(row, wanted):
    nw = norm(wanted)
    for key, value in row.items():
        if norm(key) == nw:
            return value
    return ""


def product_name(row):
    return field(row, "Naziv proizvoda")


def category_name(row):
    return field(row, "NAZIV KATEGORIJE")


def category_code(row):
    raw = str(field(row, "KATEGORIJA") or "").strip()
    raw = raw.lstrip("0") or "0"
    return raw


def format_name(row):
    return field(row, "Naziv trgovca - formata") or field(
        row, "Naziv trgovca – formata"
    )


def price_regular_raw(row):
    return parse_number(field(row, "Redovna cena"))


def price_sale_raw(row):
    return parse_number(field(row, "Snižena cena"))


def price_unit_raw(row):
    return parse_number(field(row, "Cena po jedinici mere"))


def price_list_type(row):
    return norm(field(row, "VRSTA_CENOVNIKA"))


def is_current_price_list(row):
    value = price_list_type(row)
    return not value or "vazeci_cenovnik" in value


def sale_is_active(row, today=TODAY):
    sale = price_sale_raw(row)
    if sale is None:
        return False

    start = parse_date(field(row, "Datum početka sniženja"))
    end = parse_date(field(row, "Datum kraja sniženja"))

    if start and today < start:
        return False
    if end and today > end:
        return False
    return True


def term_matches(text, term):
    ts = tokens(text)
    t = norm(term)
    if t.endswith("*"):
        prefix = t[:-1]
        return any(tok.startswith(prefix) for tok in ts)

    pts = tokens(t)
    if len(pts) == 1:
        return pts[0] in ts
    return " ".join(pts) in " ".join(ts)


def parse_package(product, name):
    """Parse fixed pack size/count, while separately marking variable-weight goods."""
    n = norm(name)

    variable_weight = (
        bool(re.search(r"\bcca\.?\s*\d", n))
        or bool(re.search(r"\bca\.?\s*\d", n))
        or bool(re.search(r"\brf\b", n))
        or "rinfuz" in n
        or "na meru" in n
    )

    result = {
        "kg": None,
        "l": None,
        "count": None,
        "variable_weight": variable_weight,
    }

    # Piece count: 30kom, 10/1, M6/1 ...
    if product.get("count_from_name"):
        m = re.search(r"(?<!\d)(\d{1,3})\s*kom\b", n)
        if not m:
            m = re.search(r"(?<!\d)(\d{1,3})\s*/\s*1\b", n)
        if m:
            result["count"] = int(m.group(1))

    # Multipacks: 2x125g, 3 x 160 ml ...
    multi = re.search(
        r"(?<!\d)(\d{1,2})\s*[xх*]\s*(\d+(?:[.,]\d+)?)\s*(kg|g|gr|l|ml)\b",
        n,
    )
    if multi:
        mult = int(multi.group(1))
        amount = float(multi.group(2).replace(",", "."))
        unit = multi.group(3)
        if unit == "kg":
            result["kg"] = mult * amount
        elif unit in ("g", "gr"):
            result["kg"] = mult * amount / 1000.0
        elif unit == "l":
            result["l"] = mult * amount
        elif unit == "ml":
            result["l"] = mult * amount / 1000.0

    # Fractions such as 1/2KG = 0.5 kg.
    if result["kg"] is None:
        frac_kg = re.search(
            r"(?<!\d)(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)\s*kg\b",
            n,
        )
        if frac_kg:
            num = float(frac_kg.group(1).replace(",", "."))
            den = float(frac_kg.group(2).replace(",", "."))
            if den:
                result["kg"] = num / den

    if result["kg"] is None:
        kg_matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*kg\b", n)
        g_matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:g|gr)\b", n)
        if kg_matches:
            result["kg"] = float(kg_matches[-1].replace(",", "."))
        elif g_matches:
            result["kg"] = float(g_matches[-1].replace(",", ".")) / 1000.0
        elif product.get("slash_one_is_kg"):
            m = re.search(r"(?<!\d)(\d{1,2})\s*/\s*1\b", n)
            if m:
                result["kg"] = float(m.group(1))

    if result["l"] is None:
        l_matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*l\b", n)
        ml_matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*ml\b", n)
        if l_matches:
            result["l"] = float(l_matches[-1].replace(",", "."))
        elif ml_matches:
            result["l"] = float(ml_matches[-1].replace(",", ".")) / 1000.0

    return result


def category_matches(row, product):
    allowed_codes = {str(x).lstrip("0") or "0" for x in product.get("category_codes_any", [])}
    code = category_code(row)
    if allowed_codes and code:
        return code in allowed_codes

    allowed_names = product.get("categories_any", [])
    if allowed_names:
        ncat = norm(category_name(row))
        return any(norm(name) in ncat for name in allowed_names)

    return True


def match_product(row, product):
    name = product_name(row)
    if not name:
        return False, "missing_name", None

    if not category_matches(row, product):
        return False, "category", None

    for group in product.get("include_groups", []):
        if not any(term_matches(name, term) for term in group):
            return False, "required_term", None

    for term in product.get("exclude", []):
        if term_matches(name, term):
            return False, "excluded_term", None

    regexes = product.get("name_regex_any", [])
    if regexes:
        nname = norm(name)
        if not any(re.search(pattern, nname) for pattern in regexes):
            return False, "regex", None

    pack = parse_package(product, name)

    max_kg = product.get("max_package_kg")
    if max_kg is not None and pack.get("kg") is not None and not pack.get("variable_weight"):
        if pack["kg"] > float(max_kg):
            return False, "package_too_large", pack

    max_count = product.get("max_count")
    if max_count is not None and pack.get("count") is not None:
        if pack["count"] > int(max_count):
            return False, "count_too_large", pack

    return True, "ok", pack


def normalize_prices(row, product, pack):
    """
    Return comparable regular/sale/current prices in the product's target unit.

    Important feed quirks handled here:
    - fixed pack (e.g. 500 g): derive unit price from pack price;
    - ca./cca/RF/rinfuz: price is treated as already per kg/l;
    - eggs: divide pack price by piece count;
    - active sale on fixed pack: divide sale pack price by pack size;
    - official 'Cena po jedinici mere' is a fallback, not blindly trusted.
    """
    target_unit = product["unit"]
    regular_raw = price_regular_raw(row)
    sale_raw = price_sale_raw(row) if sale_is_active(row) else None
    official_unit = price_unit_raw(row)
    raw_unit = norm(field(row, "Jedinica mere"))
    warnings = []

    if regular_raw is None or regular_raw <= 0:
        return None

    regular = None
    sale = None
    method = None

    if target_unit == "RSD/kom":
        count = pack.get("count")
        if product.get("count_from_name") and count:
            regular = regular_raw / count
            sale = sale_raw / count if sale_raw is not None else None
            method = "pack_count"
        else:
            regular = regular_raw
            sale = sale_raw
            method = "per_piece"

    elif target_unit == "RSD/kg":
        if pack.get("variable_weight"):
            regular = official_unit if official_unit and official_unit > 0 else regular_raw
            sale = sale_raw
            method = "variable_weight"
        elif pack.get("kg") and pack["kg"] > 0:
            regular = regular_raw / pack["kg"]
            sale = sale_raw / pack["kg"] if sale_raw is not None else None
            method = "fixed_pack_weight"
            if official_unit and official_unit > 0:
                ratio = regular / official_unit
                if ratio < 0.8 or ratio > 1.25:
                    warnings.append("official_unit_mismatch")
        elif raw_unit in ("kg", "kilogram", "kilograma"):
            regular = official_unit if official_unit and official_unit > 0 else regular_raw
            sale = sale_raw
            method = "sold_by_kg"
        elif official_unit and official_unit > 0:
            regular = official_unit
            # Without a known pack size, a sale price may be a pack price. Do not fake a unit sale.
            method = "official_unit_fallback"
            if sale_raw is not None:
                warnings.append("sale_not_normalized")
        else:
            regular = regular_raw
            sale = sale_raw
            method = "raw_fallback"
            warnings.append("unit_fallback")

    elif target_unit == "RSD/l":
        if pack.get("variable_weight"):
            regular = official_unit if official_unit and official_unit > 0 else regular_raw
            sale = sale_raw
            method = "variable_volume"
        elif pack.get("l") and pack["l"] > 0:
            regular = regular_raw / pack["l"]
            sale = sale_raw / pack["l"] if sale_raw is not None else None
            method = "fixed_pack_volume"
            if official_unit and official_unit > 0:
                ratio = regular / official_unit
                if ratio < 0.8 or ratio > 1.25:
                    warnings.append("official_unit_mismatch")
        elif raw_unit in ("l", "lit", "litar", "litara"):
            regular = official_unit if official_unit and official_unit > 0 else regular_raw
            sale = sale_raw
            method = "sold_by_l"
        elif official_unit and official_unit > 0:
            regular = official_unit
            method = "official_unit_fallback"
            if sale_raw is not None:
                warnings.append("sale_not_normalized")
        else:
            regular = regular_raw
            sale = sale_raw
            method = "raw_fallback"
            warnings.append("unit_fallback")

    else:
        regular = regular_raw
        sale = sale_raw
        method = "raw"

    if regular is None or regular <= 0:
        return None

    current = sale if sale is not None and sale > 0 else regular

    # Coarse sanity bounds; product-specific matching does the real filtering.
    lower, upper = {
        "RSD/kg": (10, 10000),
        "RSD/l": (10, 10000),
        "RSD/kom": (1, 20000),
    }.get(target_unit, (0.01, 1000000))
    if not (lower <= current <= upper):
        return None

    discount_pct = None
    if sale is not None and regular > 0 and sale < regular:
        discount_pct = round((regular - sale) / regular * 100, 1)

    return {
        "current": round(current, 2),
        "regular": round(regular, 2),
        "sale": round(sale, 2) if sale is not None else None,
        "is_sale": sale is not None and sale < regular,
        "discount_pct": discount_pct,
        "method": method,
        "warnings": warnings,
        "raw_regular": regular_raw,
        "raw_sale": sale_raw,
        "raw_official_unit": official_unit,
    }


def source_row_allowed(src, row):
    allowed = src.get("allowed_formats_exact", [])
    if allowed:
        current = norm(format_name(row))
        return current in {norm(x) for x in allowed}
    return True


def status_for(price, target, near_percent):
    if price <= target:
        return "KUPI"
    if price <= target * (1 + near_percent / 100.0):
        return "BLIZU"
    return "CEKAJ"


def parse_csv_bytes(content):
    text = content.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=";,\t")
        delimiter = dialect.delimiter
    except Exception:
        delimiter = ";"

    rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    if not rows:
        raise ValueError("CSV has no rows")

    headers = {norm(h) for h in (rows[0].keys() if rows[0] else []) if h}
    required = {norm("Naziv proizvoda"), norm("Redovna cena")}
    if not required.issubset(headers):
        raise ValueError("Downloaded content does not look like the expected price-list CSV")
    return rows


def fetch_source(src):
    errors = []
    for url in src.get("urls", []):
        if not url:
            continue
        try:
            response = requests.get(
                url,
                timeout=60,
                headers={
                    "User-Agent": "Mozilla/5.0 MojeCene/1.0",
                    "Accept": "text/csv,text/plain,*/*",
                },
            )
            response.raise_for_status()
            if len(response.content) < 100:
                raise ValueError("downloaded file is suspiciously small")
            rows = parse_csv_bytes(response.content)
            return rows, url, None
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    return None, None, " | ".join(errors) if errors else "no configured URL"


def load_previous_results():
    path = ROOT / "docs" / "results.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return data.get("results", [])
    except Exception:
        return []


def build_results_for_source(src, rows, products, settings):
    near_percent = settings.get("near_percent", 10)
    debug_limit = settings.get("debug_candidates_per_product", 8)

    usable = [
        row for row in rows
        if is_current_price_list(row) and source_row_allowed(src, row)
    ]

    selected = []
    debug = {}

    for product in products:
        candidates = []
        rejected = Counter()

        for row in usable:
            ok, reason, pack = match_product(row, product)
            if not ok:
                rejected[reason] += 1
                continue

            normalized = normalize_prices(row, product, pack)
            if normalized is None:
                rejected["price"] += 1
                continue

            candidates.append({
                "price": normalized["current"],
                "row": row,
                "pack": pack,
                "normalized": normalized,
            })

        candidates.sort(key=lambda x: x["price"])
        debug[product["id"]] = {
            "accepted": [
                {
                    "name": product_name(c["row"]),
                    "price": c["price"],
                    "method": c["normalized"]["method"],
                    "warnings": c["normalized"]["warnings"],
                    "package": c["pack"],
                }
                for c in candidates[:debug_limit]
            ],
            "rejected_counts": dict(rejected),
        }

        if not candidates:
            continue

        chosen = candidates[0]
        row = chosen["row"]
        pack = chosen["pack"]
        normalized = chosen["normalized"]
        price = normalized["current"]

        selected.append({
            "date": str(TODAY),
            "generated_at": NOW.isoformat(),
            "parser_version": PARSER_VERSION,
            "source_id": src["id"],
            "store": src["name"],
            "product_id": product["id"],
            "product": product["name"],
            "category": product["category"],
            "matched_product": product_name(row),
            "source_category": category_name(row),
            "store_format": format_name(row),
            "price": price,
            "regular_price": normalized["regular"],
            "sale_price": normalized["sale"],
            "is_sale": normalized["is_sale"],
            "discount_pct": normalized["discount_pct"],
            "target": product["target_price"],
            "unit": product["unit"],
            "status": status_for(price, product["target_price"], near_percent),
            "package": pack,
            "price_method": normalized["method"],
            "warnings": normalized["warnings"],
            "barcode": field(row, "Barkod proizvoda"),
            "price_list_date": field(row, "Datum cenovnika"),
            "stale": False,
        })

    return selected, debug, len(usable)


def main():
    product_cfg = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
    products = product_cfg["products"]
    settings = product_cfg.get("settings", {})
    sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))["sources"]

    previous = load_previous_results()
    previous_by_source = defaultdict(list)
    for item in previous:
        sid = item.get("source_id")
        if sid:
            previous_by_source[sid].append(item)

    all_results = []
    source_status = []
    debug_payload = {}
    success_count = 0

    for src in sources:
        if not src.get("enabled"):
            source_status.append({
                "source_id": src["id"],
                "store": src["name"],
                "enabled": False,
                "ok": None,
                "note": src.get("note", ""),
            })
            continue

        rows, used_url, error = fetch_source(src)
        if rows is None:
            carried = []
            for old in previous_by_source.get(src["id"], []):
                old = dict(old)
                old["stale"] = True
                old["source_error"] = error
                carried.append(old)
            all_results.extend(carried)
            source_status.append({
                "source_id": src["id"],
                "store": src["name"],
                "enabled": True,
                "ok": False,
                "error": error,
                "carried_previous_results": len(carried),
            })
            continue

        selected, debug, usable_rows = build_results_for_source(
            src, rows, products, settings
        )
        all_results.extend(selected)
        debug_payload[src["id"]] = debug
        success_count += 1
        source_status.append({
            "source_id": src["id"],
            "store": src["name"],
            "enabled": True,
            "ok": True,
            "used_url": used_url,
            "downloaded_rows": len(rows),
            "usable_current_rows": usable_rows,
            "selected_results": len(selected),
        })

    if success_count == 0:
        raise SystemExit("No enabled source could be refreshed; refusing to overwrite results.")

    order = {"KUPI": 0, "BLIZU": 1, "CEKAJ": 2}
    all_results.sort(
        key=lambda x: (
            bool(x.get("stale")),
            order.get(x.get("status"), 9),
            x.get("product", ""),
            x.get("price", 10**9),
        )
    )

    payload = {
        "generated_at": NOW.isoformat(),
        "date": str(TODAY),
        "parser_version": PARSER_VERSION,
        "results": all_results,
        "sources": source_status,
    }

    docs = ROOT / "docs"
    history = ROOT / "data" / "history"
    debug_dir = ROOT / "data" / "debug"
    docs.mkdir(exist_ok=True)
    history.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    (docs / "results.json").write_text(text, encoding="utf-8")
    (docs / "status.json").write_text(
        json.dumps(source_status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (history / f"{TODAY}.json").write_text(text, encoding="utf-8")
    (debug_dir / f"{TODAY}.json").write_text(
        json.dumps(
            {
                "generated_at": NOW.isoformat(),
                "parser_version": PARSER_VERSION,
                "sources": debug_payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Generated {len(all_results)} results from {success_count} refreshed sources.")
    for status in source_status:
        if status.get("enabled"):
            print(status)


if __name__ == "__main__":
    main()
