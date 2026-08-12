
---

# 🚀 SERVILION-BACKEND: Manual de Despliegue y Desarrollo

Este repositorio contiene la arquitectura backend de **Servilion**, construida sobre **Django 5.x** y **Django Ninja**.

Servilion es una lavandería industrial de Antofagasta que atiende campamentos mineros. El sistema reemplaza una base Access heredada y cubre **dos servicios distintos** que comparten planta pero no proceso: la ropa personal de los trabajadores en faena y la lencería a granel de la hotelería del campamento.

> El detalle operativo del negocio vive en [`FLUJO_NEGOCIO.md`](FLUJO_NEGOCIO.md); el mapeo con el sistema Access heredado, en [`DB_DICCIONARIO.md`](DB_DICCIONARIO.md). Este README describe qué hace el sistema y cómo levantarlo.

---

## ✨ Funcionalidades

### 👕 Servicio a trabajadores — guías (`orders`)

La unidad es la **guía**: el morral de una persona, que debe volver completo a su habitación.

* **Digitalización de la OT física** que llega con la ropa sucia a Antofagasta, con detalle de prendas (incluidas las que el trabajador escribe a mano, fuera de catálogo).
* **Trazabilidad en tres capas** (§5 del flujo de negocio), las tres pistoleables y resolubles a la misma guía:
  * **OT** — identificador formal, único en el histórico.
  * **`ref`** — código operativo corto (`P1238`), con correlativo semanal que se reinicia en 1000.
  * **Código de control** — el impreso en la boleta.
* **Etiquetas lavables por prenda**: el sistema emite una etiqueta por tipo de prenda declarado, con un QR que codifica `ref-código` (ej. `P1005-ALM`). Se pega a la prenda y sobrevive al lavado industrial.
* **Pistoleo unificado de empaque**: un solo endpoint resuelve todo lo que hay sobre la mesa, sin que el operador elija modo.
  * Boleta del morral → lo **abre**; pistoleada de nuevo → lo **cierra**.
  * Etiqueta de prenda → abre el morral (si hacía falta) y **marca la prenda en el mismo disparo**.
  * Sobre una guía ya cerrada como incompleta → registra la prenda como **encontrada**.
  * Si un `ref` reciclado calza con más de un morral abierto, responde `409` con las guías candidatas para que el operador elija por trabajador y empresa.
* **Control de faltantes**: al cerrar el empaque la guía queda Completada o **Incompleta**; cada prenda que falta se resuelve encontrándola o reponiéndola con su costo.
* **Boleta impresa** que acompaña la ropa limpia de vuelta a faena, con el código de entrega en QR.
* **Entrega en habitación** (Flujo 1) mediante doble escaneo —guía y QR de la puerta—, con app móvil **offline-first**: cola local, sincronización en lote y resolución de conflictos *last-write-wins* con registro de los descartes.

### 🛏 Servicio de hotelería — lotes (`hospitality`)

La unidad es el **lote**: una carga a granel del campamento que no es de nadie en particular y vuelve al mandante. Es un módulo **separado a propósito**, no una variante de las guías: aquí no hay persona, ni habitación, ni entrega individual, y la prenda no se pistolea una por una.

* **Recepción de la carga** por tipo de lencería, con peso y numeración anual propia (`H-2026-0001`).
* **Cuenta de salida y merma** — el control que el servicio a trabajadores no tiene: se registra cuánto entró y cuánto volvió de cada tipo, y la diferencia se asume pérdida y se informa. Un lote sin contar tiene merma *desconocida*, no cero.
* **Acta de devolución** imprimible con el conteo, las diferencias y espacio de firma del encargado en faena.
* **Indicadores del servicio**: lotes en planta, piezas procesadas, peso y tasa de merma acumulada.

> El sistema Access heredado no sabía representar esto y lo forzó dentro de las guías, creando trabajadores falsos llamados `200 JUEGOS DE SABANAS` —con RUT `0` y una habitación inventada—. Este módulo existe para no repetir ese apaño.

### 🗂 Administración y soporte

* **Clientes y empresas** — el cliente agrupa empresas y concentra el catálogo de precios; la empresa define su **tipo de servicio** (`PERSONAL` u `HOTELERIA`), su modalidad de entrega (Flujo 1 o 2), su tipo de cobro y su logo.
* **Campamentos y habitaciones**, con **QR por puerta** e impresión de etiquetas para pegarlas.
* **Trabajadores**, **catálogo de prendas** y **precios por cliente**.
* **Reportería**: torre de control operativa (resumen, series de tiempo, guías atascadas) y panel de calidad e incidencias.
* **Autenticación JWT** con cuatro roles operativos: `ADMIN`, `SUPERVISOR`, `DIGITADOR_OT` y `DIGITADOR_EMPAQUE`.

### 🔌 Superficie de la API

Documentación interactiva en `http://localhost:8000/api/docs`.

| Módulo | Prefijo | Endpoints |
|---|---|---|
| Autenticación | `/api/auth/` | 3 |
| Clientes | `/api/clients/` | 8 |
| Empresas | `/api/companies/` | 7 |
| Trabajadores | `/api/workers/` | 5 |
| Prendas | `/api/garments/` | 4 |
| Campamentos | `/api/camps/` | 5 |
| Habitaciones | `/api/rooms/` | 5 |
| Guías | `/api/orders/` | 22 |
| Entrega en habitación | `/api/delivery/` | 1 |
| **Hotelería** | `/api/hospitality/` | 8 |
| Reportes | `/api/reports/` | 6 |

---

## 🛠 Requisitos Previos

* [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado y en ejecución.
* Git instalado.
* Puerto `80` y `8000` disponibles en tu máquina local.

## 📦 Estructura del Proyecto

```text
/
├── core/               # Configuración de Django, Celery y registro de routers
├── common/             # Utilidades compartidas (modelos base, S3, importador legado)
├── authentication/     # Usuarios del staff, JWT y roles
│
├── companies/          # Clientes, empresas y catálogo de precios por cliente
├── camps/              # Campamentos, habitaciones y sus QR
├── workers/            # Trabajadores en faena
├── garments/           # Catálogo de tipos de prenda
│
├── orders/             # Servicio a trabajadores: guías, empaque, entrega y reportes
├── hospitality/        # Servicio de hotelería: lotes de lencería y merma
│
├── nginx/              # Configuración del Proxy Inverso
├── .env / .env.prod    # Variables de entorno (desarrollo / producción)
├── docker-compose.yml  # Orquestador de desarrollo (Runserver)
└── Dockerfile          # Imagen base del contenedor

```

Cada app de dominio sigue la misma estructura: `models.py`, `schemas.py`, `services.py` y `api.py` (ver [Reglas de Arquitectura](#️-reglas-de-arquitectura-service-layer)).

---

## 🏃‍♂️ Instrucciones de Inicio Rápido (Desarrollo)

Para levantar el entorno local, asegúrate de que Docker Desktop esté abierto y ejecuta los siguientes comandos en la terminal desde la raíz del proyecto:

### 1. Construcción del entorno

```bash
docker compose build

```

### 2. Generación del proyecto (Solo la primera vez)

Si la carpeta `core` aún no existe, inicialízala con:

```bash
docker compose run --rm api django-admin startproject core .

```

### 3. Ejecución del servidor

```bash
docker compose up -d

```

* Tu API estará disponible en: `http://localhost:8000`

---

## 🏗 Instrucciones de Producción (EC2)

Para desplegar en el servidor de producción (AWS), utilizamos el archivo de configuración `docker-compose.prod.yml`, que integra **Gunicorn**, **Uvicorn** y **Nginx**.

### 1. Despliegue inicial

```bash
docker compose -f docker-compose.prod.yml up -d --build

```

### 2. Recolección de archivos estáticos

Es obligatorio ejecutar esto para que Nginx pueda servir el panel de administración correctamente:

```bash
docker compose -f docker-compose.prod.yml run --rm api python manage.py collectstatic --no-input

```

---

## ⚙️ Reglas de Arquitectura (Service Layer)

Para mantener la coherencia del sistema, **no programar lógica dentro de las vistas (`api.py`)**. Sigue estrictamente este patrón:

* **`models.py`**: Definición de tablas de base de datos.
* **`schemas.py`**: Validación de entrada/salida (Pydantic).
* **`services.py`**: **Aquí reside la lógica de negocio.** Toda consulta a la BD se hace aquí.
* **`api.py`**: Solo enruta las peticiones y llama a los servicios.

## 📝 Comandos Útiles

* **Ver los logs de los contenedores:**
```bash
docker compose logs -f api

```


* **Entrar a la terminal del contenedor:**
```bash
docker compose exec api bash

```


* **Detener todo el sistema:**
```bash
docker compose down

```



---