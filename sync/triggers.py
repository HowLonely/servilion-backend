"""Triggers de PostgreSQL que alimentan `sync_changelog` y el aplicador que los silencia."""

from contextlib import contextmanager

from django.db import connection

# Variable de sesión con la que la sincronización marca sus propias escrituras.
# `SET LOCAL` dura hasta el fin de la transacción; igual se apaga a mano al
# salir del bloque, porque en las pruebas la transacción envuelve todo el test.
APPLYING_SETTING = 'servilion.sync_applying'

# Canal de LISTEN/NOTIFY: el proceso de sincronización del servidor local se
# despierta apenas se confirma un cambio, en vez de esperar al próximo sondeo.
NOTIFY_CHANNEL = 'servilion_sync'

FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION sync_track_change() RETURNS trigger AS $$
BEGIN
    IF current_setting('{APPLYING_SETTING}', true) = 'on' THEN
        RETURN NULL;
    END IF;
    IF TG_OP = 'DELETE' THEN
        INSERT INTO sync_changelog ("table", row_id, op) VALUES (TG_TABLE_NAME, OLD.id, 'D');
    ELSE
        INSERT INTO sync_changelog ("table", row_id, op) VALUES (TG_TABLE_NAME, NEW.id, 'U');
    END IF;
    PERFORM pg_notify('{NOTIFY_CHANNEL}', TG_TABLE_NAME);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

DROP_FUNCTION_SQL = 'DROP FUNCTION IF EXISTS sync_track_change() CASCADE;'


def trigger_sql(table: str) -> str:
    return (
        f'DROP TRIGGER IF EXISTS sync_track ON "{table}";\n'
        f'CREATE TRIGGER sync_track AFTER INSERT OR UPDATE OR DELETE ON "{table}" '
        f'FOR EACH ROW EXECUTE FUNCTION sync_track_change();'
    )


def drop_trigger_sql(table: str) -> str:
    return f'DROP TRIGGER IF EXISTS sync_track ON "{table}";'


def install(tables, cursor=None) -> None:
    def run(cur):
        cur.execute(FUNCTION_SQL)
        for table in tables:
            cur.execute(trigger_sql(table))

    if cursor is not None:
        run(cursor)
    else:
        with connection.cursor() as cur:
            run(cur)


@contextmanager
def applying():
    """Marca las escrituras del bloque como de la sincronización (sin changelog).

    Debe usarse dentro de una transacción. También fija las restricciones de
    FK como inmediatas: Django las crea diferibles, y así un error de una fila
    aparece en esa fila (y su savepoint) en vez de tumbar el lote al confirmar.
    """
    with connection.cursor() as cursor:
        cursor.execute('SELECT set_config(%s, %s, true)', [APPLYING_SETTING, 'on'])
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SELECT set_config(%s, %s, true)', [APPLYING_SETTING, 'off'])
            cursor.execute('SET CONSTRAINTS ALL DEFERRED')
