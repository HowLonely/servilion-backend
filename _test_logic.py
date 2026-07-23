import re, unicodedata, sys
sys.stdout.reconfigure(encoding='utf-8')
def strip_accents(t): return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
def norm(t):
    if t is None: return ""
    return re.sub(r"\s+"," ",strip_accents(str(t)).upper()).strip()
ITEM = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")

# match item names to prendas catalog (real)
import pyodbc
MDB=r"c:\Users\WorldMS\Desktop\Proyecto Servilion\servilion-backend\ejemplo_db_penon.mdb"
conn=pyodbc.connect(r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ="+MDB+";")
cur=conn.cursor()
cur.execute("SELECT prenda FROM prendas")
catalog={norm(r[0]) for r in cur.fetchall()}
print("catalogo prendas:", len(catalog))

cur.execute("SELECT TOP 20000 item FROM guias WHERE item IS NOT NULL AND item<>''")
matched=unmatched=0; badtoken=0
from collections import Counter
missing=Counter()
for (it,) in cur.fetchall():
    for tok in str(it).split("+"):
        m=ITEM.match(tok)
        if not m: 
            if tok.strip(): badtoken+=1
            continue
        name=m.group(2).strip()
        if norm(name) in catalog: matched+=1
        else: unmatched+=1; missing[norm(name)]+=1
print(f"tokens: matched={matched} unmatched(autocrea)={unmatched} badtoken(skip)={badtoken}")
print("top nombres que se autocrearian:", missing.most_common(15))

# dedup OT determinista
print("\n-- dedup OT --")
cur.execute("SELECT Id, ot FROM guias ORDER BY Id")
used=set(); assigned=[]; clash=0
def resolve(ot, idv, used):
    ot=(ot or "").strip()[:20]
    if ot and ot not in ("0","00","000000") and ot not in used: return ot
    return f"{ot or 'SOT'}-L{idv}"[:20]
n=0
for idv,ot in cur.fetchall():
    on=resolve(ot,idv,used)
    if on in used: clash+=1
    used.add(on); n+=1
print(f"guias={n} order_numbers unicos={len(used)} colisiones={clash}")
