import csv, io, json, re, unicodedata
from pathlib import Path
from datetime import date
import requests

ROOT=Path(__file__).resolve().parent
def norm(s):
    s=str(s or "").lower()
    return "".join(c for c in unicodedata.normalize("NFKD",s) if not unicodedata.combining(c))

def fetch(src):
    for url in (src.get("url"), src.get("fallback")):
        if not url: continue
        try:
            r=requests.get(url,timeout=45,headers={"User-Agent":"Mozilla/5.0 PriceTracker/1.0"})
            r.raise_for_status()
            return r.content
        except Exception as e:
            print(f"{src['name']}: failed {url}: {e}")
    return None

def parse(content):
    text=content.decode("utf-8-sig",errors="replace")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))

def row_text(row):
    return norm(" ".join(str(v) for v in row.values() if v))

def find_price(row):
    # Schema differs slightly by merchant; prefer fields that look like current/sale/retail price.
    candidates=[]
    for k,v in row.items():
        nk=norm(k)
        if any(x in nk for x in ["cena","price"]):
            try:
                num=float(str(v).replace(".","").replace(",",".").strip())
                if 0 < num < 1000000: candidates.append((k,num))
            except: pass
    return candidates[0][1] if candidates else None

def main():
    products=json.loads((ROOT/"products.json").read_text(encoding="utf-8"))["products"]
    sources=json.loads((ROOT/"sources.json").read_text(encoding="utf-8"))["sources"]
    out=[]
    for src in sources:
        if not src.get("enabled"): continue
        content=fetch(src)
        if not content: continue
        try: rows=parse(content)
        except Exception as e:
            print(src["name"],"parse error",e); continue
        print(src["name"],len(rows),"rows")
        for p in products:
            inc=[norm(x) for x in p["include"]]; exc=[norm(x) for x in p["exclude"]]
            matches=[]
            for row in rows:
                t=row_text(row)
                if all(x in t for x in inc) and not any(x in t for x in exc):
                    price=find_price(row)
                    if price is not None: matches.append((price,row))
            if matches:
                price,row=min(matches,key=lambda x:x[0])
                status="KUPI" if price <= p["target_price"] else ("BLIZU" if price <= p["target_price"]*1.10 else "CEKAJ")
                out.append({"date":str(date.today()),"store":src["name"],"product_id":p["id"],
                            "product":p["name"],"price":price,"target":p["target_price"],
                            "unit":p["unit"],"status":status,"raw":row})
    (ROOT/"docs").mkdir(exist_ok=True)
    (ROOT/"data/history").mkdir(parents=True,exist_ok=True)
    (ROOT/"docs/results.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    (ROOT/f"data/history/{date.today()}.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print("saved",len(out),"matches")

if __name__=="__main__": main()
