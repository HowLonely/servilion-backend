import pyodbc, sys
sys.stdout.reconfigure(encoding='utf-8')
MDB = r"c:\Users\WorldMS\Desktop\Proyecto Servilion\servilion-backend\ejemplo_db_penon.mdb"
conn = pyodbc.connect(r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=" + MDB + ";")
cur = conn.cursor()
def scalar(sql):
    cur.execute(sql); return cur.fetchone()[0]

cur.execute("SELECT ot, COUNT(*) FROM guias GROUP BY ot HAVING COUNT(*)>1 ORDER BY COUNT(*) DESC")
dups = cur.fetchall()
print("cuantos ot tienen duplicados:", len(dups))
print("top dups:", [(r[0], r[1]) for r in dups[:8]])

print("\nempresa Id=3 repr:")
cur.execute("SELECT empresa FROM empresas WHERE Id=3"); print(repr(cur.fetchone()[0]))

print("\nempresas distintas en guias:", scalar("SELECT COUNT(*) FROM (SELECT DISTINCT empresa FROM guias)"))
cur.execute("SELECT DISTINCT g.empresa FROM guias g LEFT JOIN empresas e ON g.empresa=e.empresa WHERE e.empresa IS NULL")
missing = [r[0] for r in cur.fetchall()]
print("en guias pero NO en empresas (total):", len(missing), "-> ej:", missing[:10])

print("\ncodigo vacio/null en guias:", scalar("SELECT COUNT(*) FROM guias WHERE codigo IS NULL OR codigo=''"))
print("(empresa,codigo) distintos en guias:", scalar("SELECT COUNT(*) FROM (SELECT DISTINCT empresa,codigo FROM guias)"))
print("prendas nombre distintos:", scalar("SELECT COUNT(*) FROM (SELECT DISTINCT prenda FROM prendas)"),
      "codigo distintos:", scalar("SELECT COUNT(*) FROM (SELECT DISTINCT codigo FROM prendas)"))
print("usuarios (empresa,codigo) distintos:", scalar("SELECT COUNT(*) FROM (SELECT DISTINCT empresa,codigo FROM usuarios)"),
      "de", scalar("SELECT COUNT(*) FROM usuarios"))

# muestra de item para ver tokens que no son prendas
print("\n-- tokens 'item' unicos aprox (muestra 2000 guias) --")
cur.execute("SELECT TOP 2000 item FROM guias WHERE item IS NOT NULL AND item<>''")
import re
from collections import Counter
names=Counter()
for (it,) in cur.fetchall():
    for part in str(it).split('+'):
        part=part.strip()
        m=re.match(r'^(\d+)\s+(.*)$', part)
        if m: names[m.group(2).strip().upper()]+=1
        elif part: names['<<'+part[:20]+'>>']+=1
for n,c in names.most_common(40):
    print(f"  {c:>5}  {n}")
