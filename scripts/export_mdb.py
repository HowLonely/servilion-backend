"""Extractor del Access legado (ejemplo_db_penon.mdb) -> archivos JSONL.

PASO 1 de la migración de datos. Se ejecuta en la máquina Windows de desarrollo,
que es la única con el driver ODBC de Microsoft Access. NO depende de Django ni
de PostgreSQL: sólo lee el .mdb y escribe archivos de texto (JSONL, un objeto por
línea) que luego consume el comando `manage.py import_legacy` dentro del contenedor.

Requisitos (sólo en el host Windows, NO en el contenedor):
    - Driver "Microsoft Access Driver (*.mdb, *.accdb)" de 64 bits
      (viene con "Microsoft Access Database Engine 2016 Redistributable" x64).
    - pip install pyodbc

Uso:
    python scripts/export_mdb.py                       # usa ./ejemplo_db_penon.mdb
    python scripts/export_mdb.py --mdb otra.mdb --out legacy_export

Salida (carpeta legacy_export/):
    companies.jsonl   <- tabla empresas
    garments.jsonl    <- tabla prendas
    workers.jsonl     <- tabla usuarios
    orders.jsonl      <- tabla guias (282k filas, se escribe en streaming)
"""
from __future__ import annotations

import argparse
import datetime as dt
import decimal
import json
import os
import sys

try:
    import pyodbc
except ImportError:
    sys.exit("Falta pyodbc. Instálalo en el host con: pip install pyodbc")

ACCESS_DRIVER = "{Microsoft Access Driver (*.mdb, *.accdb)}"


def _connect(mdb_path: str) -> "pyodbc.Connection":
    if not os.path.isfile(mdb_path):
        sys.exit(f"No existe el archivo .mdb: {mdb_path}")
    drivers = [d for d in pyodbc.drivers() if "Access" in d]
    if not any("*.accdb" in d for d in drivers):
        sys.exit(
            "No se encontró el driver de Access de 64 bits.\n"
            "Instala 'Microsoft Access Database Engine 2016 Redistributable' (x64).\n"
            f"Drivers detectados: {drivers}"
        )
    return pyodbc.connect(f"DRIVER={ACCESS_DRIVER};DBQ={os.path.abspath(mdb_path)};")


# Epoch de las fechas de Access: días desde 1899-12-30 (número de serie OLE).
_ACCESS_EPOCH = dt.datetime(1899, 12, 30)


def _serial_to_iso(serial):
    """Convierte el número de serie OLE de una fecha Access a texto ISO.

    Se usa CDbl() en la consulta (en vez de leer la fecha directo) porque hay
    valores fuera de rango que el driver ODBC no logra convertir a datetime.
    """
    if serial in (None, ""):
        return None
    try:
        result = _ACCESS_EPOCH + dt.timedelta(days=float(serial))
    except (OverflowError, ValueError):
        return None
    if result.year < 1990 or result.year > 2100:
        return None  # fecha centinela/corrupta del legado
    return result.isoformat(sep=" ")


def _clean(value):
    """Normaliza un valor de Access a algo serializable en JSON."""
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        # Access guarda fechas naive; las serializamos ISO y el importador
        # les pone la zona horaria (America/Santiago) al cargar.
        return value.isoformat(sep=" ")
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (bytes, bytearray)):
        return None  # logos/binarios: se manejan por S3, no se migran aquí
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def _dump_query(cur, sql: str, columns: list[str], out_path: str, date_fields=()) -> int:
    cur.execute(sql)
    date_fields = set(date_fields)
    count = 0
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        while True:
            rows = cur.fetchmany(5000)
            if not rows:
                break
            for row in rows:
                record = {}
                for col, val in zip(columns, row):
                    record[col] = _serial_to_iso(val) if col in date_fields else _clean(val)
                fh.write(json.dumps(record, ensure_ascii=False))
                fh.write("\n")
                count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta el Access legado a JSONL.")
    parser.add_argument("--mdb", default="ejemplo_db_penon.mdb", help="Ruta al archivo .mdb")
    parser.add_argument("--out", default="legacy_export", help="Carpeta de salida")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    conn = _connect(args.mdb)
    cur = conn.cursor()

    jobs = [
        (
            "companies.jsonl",
            "SELECT empresa, cobro FROM empresas",
            ["empresa", "cobro"],
            (),
        ),
        (
            "garments.jsonl",
            "SELECT prenda, codigo, valor FROM prendas",
            ["prenda", "codigo", "valor"],
            (),
        ),
        (
            "workers.jsonl",
            "SELECT nombre, codigo, empresa, turno, rut, telefono, cargo, area, patio, pieza "
            "FROM usuarios",
            ["nombre", "codigo", "empresa", "turno", "rut", "telefono", "cargo", "area", "patio", "pieza"],
            (),
        ),
        (
            "orders.jsonl",
            # ORDER BY Id garantiza que la desduplicación de 'ot' en el importador
            # sea determinista y estable entre corridas (gana el Id más bajo).
            # Las fechas se piden como número de serie (CDbl): hay valores fuera de
            # rango que el driver ODBC no puede convertir a datetime. Se reconstruyen
            # en Python (ver _serial_to_iso).
            "SELECT Id, ot, empresa, codigo, nombre, rut, patio, pieza, cargo, turno, telefono, "
            "prendas, peso, observacion, status, item, ref, control, ticket, "
            "IIF(IsNull(recepcion),Null,CDbl(recepcion)) AS f_recepcion, "
            "IIF(IsNull(rlavanderia),Null,CDbl(rlavanderia)) AS f_rlavanderia, "
            "IIF(IsNull(entrega),Null,CDbl(entrega)) AS f_entrega, "
            "IIF(IsNull(completado),Null,CDbl(completado)) AS f_completado, "
            "IIF(IsNull(despachado),Null,CDbl(despachado)) AS f_despachado, "
            "IIF(IsNull(entregado),Null,CDbl(entregado)) AS f_entregado, "
            "revisadopor, digitadopor, pesadopor "
            "FROM guias ORDER BY Id",
            ["id", "ot", "empresa", "codigo", "nombre", "rut", "patio", "pieza", "cargo", "turno",
             "telefono", "prendas", "peso", "observacion", "status", "item", "ref", "control",
             "ticket", "recepcion", "rlavanderia", "entrega", "completado", "despachado",
             "entregado", "revisadopor", "digitadopor", "pesadopor"],
            ("recepcion", "rlavanderia", "entrega", "completado", "despachado", "entregado"),
        ),
    ]

    for filename, sql, columns, date_fields in jobs:
        out_path = os.path.join(args.out, filename)
        print(f"Exportando {filename} ...", flush=True)
        n = _dump_query(cur, sql, columns, out_path, date_fields)
        print(f"  -> {n} filas en {out_path}", flush=True)

    conn.close()
    print("\nListo. Ahora ejecuta el importador dentro del contenedor:")
    print(f"  docker compose exec api_servilion python manage.py import_legacy {args.out}")


if __name__ == "__main__":
    main()
