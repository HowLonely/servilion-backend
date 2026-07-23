import pyodbc

MDB = r"c:\Users\WorldMS\Desktop\Proyecto Servilion\servilion-backend\ejemplo_db_penon.mdb"
conn = pyodbc.connect(r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=" + MDB + ";")
cur = conn.cursor()

print("===== TABLES =====")
tables = []
for row in cur.tables(tableType='TABLE'):
    tables.append(row.table_name)
for t in tables:
    print(" -", t)

print("\n===== ROW COUNTS =====")
for t in tables:
    try:
        cur.execute(f"SELECT COUNT(*) FROM [{t}]")
        print(f"{t}: {cur.fetchone()[0]}")
    except Exception as e:
        print(f"{t}: ERR {e}")
