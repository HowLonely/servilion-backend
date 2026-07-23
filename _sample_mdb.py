import pyodbc
MDB = r"c:\Users\WorldMS\Desktop\Proyecto Servilion\servilion-backend\ejemplo_db_penon.mdb"
conn = pyodbc.connect(r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=" + MDB + ";")
cur = conn.cursor()

def show(sql, n=6, label=""):
    print(f"\n----- {label} -----")
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    print(" | ".join(cols))
    for r in cur.fetchmany(n):
        print(" | ".join("" if v is None else str(v)[:40] for v in r))

show("SELECT Id,empresa,cobro FROM empresas", 25, "empresas")
show("SELECT Id,prenda,codigo,valor FROM prendas", 12, "prendas")
show("SELECT DISTINCT status FROM guias", 30, "status distintos en guias")
show("SELECT DISTINCT cobro FROM empresas", 10, "cobro distintos")
show("SELECT ot,empresa,codigo,nombre,rut,turno,prendas,peso,status,item,observacion,recepcion,entrega FROM guias", 5, "guias sample")
show("SELECT Id,nombre,codigo,empresa,turno,rut,cargo,area,patio,pieza FROM usuarios", 6, "usuarios sample")
