import pyodbc
MDB = r"c:\Users\WorldMS\Desktop\Proyecto Servilion\servilion-backend\ejemplo_db_penon.mdb"
conn = pyodbc.connect(r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=" + MDB + ";")
cur = conn.cursor()

for t in ["empresas","prendas","usuarios","guias","pendiente"]:
    print(f"\n========== {t} : COLUMNS ==========")
    for c in cur.columns(table=t):
        print(f"  {c.column_name:<25} {c.type_name:<12} size={c.column_size}")
