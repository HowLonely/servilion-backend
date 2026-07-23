import pyodbc, sys
sys.stdout.reconfigure(encoding='utf-8')
MDB = r"c:\Users\WorldMS\Desktop\Proyecto Servilion\servilion-backend\ejemplo_db_penon.mdb"
conn = pyodbc.connect(r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=" + MDB + ";")
cur = conn.cursor()

def scalar(sql):
    cur.execute(sql); return cur.fetchone()[0]

print("guias total:", scalar("SELECT COUNT(*) FROM guias"))
print("ot nulos/vacios:", scalar("SELECT COUNT(*) FROM guias WHERE ot IS NULL OR ot=''"))
print("ot='0':", scalar("SELECT COUNT(*) FROM guias WHERE ot='0'"))
print("ot distintos:", scalar("SELECT COUNT(*) FROM (SELECT DISTINCT ot FROM guias)"))
cur.execute("SELECT TOP 8 ot, COUNT(*) c FROM guias GROUP BY ot HAVING COUNT(*)>1 ORDER BY COUNT(*) DESC")
print("ot duplicados (top):", [(r[0], r[1]) for r in cur.fetchall()])

print("\n-- encoding check nombre con ñ --")
cur.execute("SELECT empresa FROM empresas WHERE Id=3")
val = cur.fetchone()[0]
print("repr:", repr(val))

print("\n-- empresas en guias fuera de tabla empresas --")
print("empresas distintas en guias:", scalar("SELECT COUNT(*) FROM (SELECT DISTINCT empresa FROM guias)"))
cur.execute("""SELECT DISTINCT g.empresa FROM guias g LEFT JOIN empresas e ON g.empresa=e.empresa WHERE e.empresa IS NULL""")
missing = [r[0] for r in cur.fetchall()]
print("en guias pero NO en empresas:", missing[:20], "..." if len(missing)>20 else "", "total", len(missing))

print("\n-- codigo (badge) vacios en guias --")
print("codigo vacio/null:", scalar("SELECT COUNT(*) FROM guias WHERE codigo IS NULL OR codigo=''"))
print("(empresa,codigo) distintos en guias:", scalar("SELECT COUNT(*) FROM (SELECT DISTINCT empresa,codigo FROM guias)"))

print("\n-- prendas: codigos duplicados? nombres duplicados? --")
print("prendas total:", scalar("SELECT COUNT(*) FROM prendas"))
print("codigo distintos:", scalar("SELECT COUNT(*) FROM (SELECT DISTINCT codigo FROM prendas)"))
print("prenda(nombre) distintos:", scalar("SELECT COUNT(*) FROM (SELECT DISTINCT prenda FROM prendas)"))

print("\n-- usuarios: codigo duplicado dentro de misma empresa? --")
print("(empresa,codigo) distintos:", scalar("SELECT COUNT(*) FROM (SELECT DISTINCT empresa,codigo FROM usuarios)"))
print("usuarios total:", scalar("SELECT COUNT(*) FROM usuarios"))
