# Usuarios de staff por rol

Cuentas de **desarrollo** para recorrer el flujo completo (`FLUJO_NEGOCIO.md` §4)
viendo lo que ve cada puesto real. Existen porque con una sola cuenta `ADMIN` no
se prueba nada: `ADMIN` atraviesa toda restricción de rol
(`authentication/permissions.py::user_has_role`), así que el sistema se ve
siempre abierto y los 403 que sufre un operador aparecen recién en producción.

> **Nunca usar estas credenciales fuera de desarrollo.** La contraseña está
> escrita en este archivo y en `authentication/management/commands/seed_staff.py`,
> que es el que las repone.

---

## 1. Las cinco cuentas

Contraseña para las cinco: **`Servilion.2026`**

| Usuario | Rol | Nombre visible | Para qué sirve |
|---|---|---|---|
| `demo_admin` | `ADMIN` | Demo Administrador | Catálogo y dinero: clientes, faenas, empresas, trabajadores, prendas, precios y conflictos de sync. Además pasa por encima de cualquier otro permiso. |
| `demo_supervisor` | `SUPERVISOR` | Demo Supervisor | La operación completa: pesa, digitaliza, empaca, confirma la recepción del morral limpio en faena, registra la entrega en habitación y ve la reportería. No administra el catálogo. |
| `demo_pesaje` | `PESAJE` | Demo Pesaje | Estación 1 de Antofagasta: pesa el morral sucio al llegar, cuenta sus prendas e imprime las etiquetas. |
| `demo_ot` | `DIGITADOR_OT` | Demo Digitador OT | Estación 2 de Antofagasta: digitaliza la OT física que llega con la ropa sucia, y recibe las cargas de lencería del campamento. |
| `demo_empaque` | `DIGITADOR_EMPAQUE` | Demo Digitador Empaque | Estación 3 de Antofagasta: pistolea el morral limpio al empacarlo, lo despacha, resuelve las guías incompletas y cuenta la salida de la lencería. |

Dónde entran:

- **Panel web** — `http://localhost:3000/login`
- **Terminal de escritorio** (`servilion-desktop`) — misma cuenta; el menú ofrece
  solo las estaciones que el rol habilita (pesaje, digitalización, empaque y
  lencería de hotelería). `PESAJE` **solo** entra acá: su puesto no tiene ninguna
  pantalla en el panel web, que se lo dice en vez de dejarlo en un panel vacío.
- **Admin de Django** (`http://localhost:8000/admin`) — **no**: estas cuentas son
  `is_staff=False` a propósito. Su alcance es la API. Para el admin sigue estando
  el superusuario `admin`, que es anterior a este archivo.

---

## 2. Qué habilita cada rol

Matriz real, tomada de los decoradores `@require_roles` / `@require_admin` del
backend. `ADMIN` no aparece en las filas porque las atraviesa todas.

| Acción | Endpoint | Roles |
|---|---|---|
| Pesar el morral y emitir etiquetas | `POST /api/weighing/` | `PESAJE`, `SUPERVISOR` |
| Anular un pesaje | `POST /api/weighing/{id}/void` | `PESAJE`, `SUPERVISOR` |
| Digitalizar la OT | `POST /api/orders/` | `DIGITADOR_OT`, `SUPERVISOR` |
| Pistoleo único de la mesa de empaque | `POST /api/orders/scan/packing` | `DIGITADOR_EMPAQUE`, `SUPERVISOR` |
| Pistolear una prenda de una guía concreta | `POST /api/orders/{id}/packing/scan` | `DIGITADOR_EMPAQUE`, `SUPERVISOR` |
| Cerrar el empaque | `POST /api/orders/{id}/packing/finish` | `DIGITADOR_EMPAQUE`, `SUPERVISOR` |
| **Despachar el morral a faena** | `POST /api/orders/{id}/dispatch` | `DIGITADOR_EMPAQUE`, `SUPERVISOR` |
| Resolver prenda faltante | `POST /api/orders/{id}/incomplete/resolve` | `DIGITADOR_EMPAQUE`, `SUPERVISOR` |
| Recepción del morral limpio en faena | `POST /api/orders/{id}/clean-reception` | `SUPERVISOR` |
| Entrega en habitación | `POST /api/orders/{id}/deliver` | `SUPERVISOR` |
| Reportería / torre de control | `GET /api/reports/...` | `SUPERVISOR` |
| Recibir carga de lencería | `POST /api/hospitality/` | `DIGITADOR_OT`, `SUPERVISOR` |
| Contar la salida de la lencería | `POST /api/hospitality/{id}/return-count` | `DIGITADOR_EMPAQUE`, `SUPERVISOR` |
| Declarar la carga en proceso | `POST /api/hospitality/{id}/process` | `DIGITADOR_EMPAQUE`, `SUPERVISOR` |
| Despachar el lote y fijar su merma | `POST /api/hospitality/{id}/dispatch` | `SUPERVISOR` |
| Clientes, empresas, faenas, campamentos, trabajadores, prendas, precios, conflictos | `POST/PUT/DELETE` de esos módulos | `ADMIN` |

El despacho del morral es un paso propio y no el mismo botón que el cierre: un
morral cerrado sigue en el andén de la planta hasta que alguien lo carga al
camión (`FLUJO_NEGOCIO.md` §4, paso 7). Por eso lo puede el mismo puesto que
empaca, mientras que el despacho del **lote de hotelería** queda en el
supervisor: ahí el despacho es lo que fija la merma definitiva del lote.

La misma matriz está duplicada en los dos clientes, a propósito y documentada
allí: `servilion-web/src/components/layout/nav-config.ts` y
`servilion-desktop/src/renderer/src/lib/auth/capabilities.ts`. Si cambian los
permisos del backend hay que tocar los tres lugares.

`scripts/check_role_matrix.py` la verifica golpeando la API real, un usuario por
rol dentro de una transacción que siempre se revierte:

```bash
docker compose exec api_servilion python manage.py shell < scripts/check_role_matrix.py
```

### Recorrido mínimo para probar el flujo con las cinco

1. `demo_admin` — crea/ajusta cliente, faena, empresa (mandante o contratista),
   trabajador y precios.
2. `demo_pesaje` — pesa el morral eligiendo cliente y empresa: ahí nace el `ref`
   y se emiten los adhesivos por unidad (`P1375A-01`, `-02`…) más el ticket
   maestro que lee el digitador.
3. `demo_ot` — tipea ese ref en Digitalizar OT: la guía hereda el ref y el peso,
   y solo queda declarar trabajador y detalle de prendas.
4. `demo_empaque` — pistolea los adhesivos del morral, uno por prenda; si falta
   una, la guía queda `INCOMPLETA` y al reaparecer se resuelve pistoleándola.
   Después pistolea la boleta dos veces más: la primera cierra el morral y la
   segunda lo despacha a faena.
5. `demo_supervisor` — confirma la recepción del morral limpio en faena,
   registra la entrega en habitación y revisa la torre de control.

Para recorrer hotelería, que es el otro servicio: `demo_ot` recibe una carga de
lencería contra una empresa con `service_type = HOTELERIA`, `demo_empaque` cuenta
su salida —ahí aparece la merma— y `demo_supervisor` despacha el lote.

Para probar el camino de excepción, salta el paso 2 y digitaliza sin ticket: la
guía genera su propio ref y el empaque vuelve al modo por tipo de prenda.

---

## 3. Cómo reponerlas

El comando es idempotente y **repone la contraseña en cada corrida**, para que lo
que dice este archivo siga siendo cierto:

```bash
docker compose exec api_servilion python manage.py seed_staff
```

Con otra contraseña:

```bash
docker compose exec api_servilion python manage.py seed_staff --password "OtraClave.2026"
```

Verificación rápida de que autentican:

```bash
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"demo_ot","password":"Servilion.2026"}'
```

---

## 4. Cuentas anteriores (no las toca este comando)

Ya existían en la base y quedaron intactas; sus contraseñas no se administran
acá. Los nombres `recepcion` / `lavanderia` / `despacho` vienen del esquema de
roles viejo (`RECEPCION`, `LAVANDERIA`, `DESPACHO`), que se reemplazó por los
cinco roles actuales; por eso dos de ellas comparten `DIGITADOR_EMPAQUE`.

| Usuario | Rol actual | Notas |
|---|---|---|
| `admin` | `ADMIN` | Superusuario de Django; es el único que entra a `/admin`. |
| `supervisor` | `SUPERVISOR` | — |
| `recepcion` | `DIGITADOR_OT` | Nombre heredado del rol `RECEPCION`. |
| `lavanderia` | `DIGITADOR_EMPAQUE` | Nombre heredado del rol `LAVANDERIA`. |
| `despacho` | `DIGITADOR_EMPAQUE` | Nombre heredado del rol `DESPACHO`. |
