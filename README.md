
---

# 🚀 SERVILION-BACKEND: Manual de Despliegue y Desarrollo

Este repositorio contiene la arquitectura backend de **Servilion**, construida sobre **Django 5.x** y **Django Ninja**.

## 🛠 Requisitos Previos

* [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado y en ejecución.
* Git instalado.
* Puerto `80` y `8000` disponibles en tu máquina local.

## 📦 Estructura del Proyecto

```text
/
├── nginx/              # Configuración del Proxy Inverso
├── core/               # Configuración de Django y Celery
├── .env.prod           # Variables de entorno para producción
├── .env                # Variables de entorno para desarrollo
├── docker-compose.yml  # Orquestador de desarrollo (Runserver)
└── Dockerfile          # Imagen base del contenedor

```

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