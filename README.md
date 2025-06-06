# MercadoLibre Car Scraper

Scraper web en Python + Flask para consultar autos publicados en Mercado Libre Argentina, almacenar la evolución diaria de precios en MongoDB y consultar los resultados mediante una interfaz web moderna con Bootstrap, DataTables y visualización de históricos.

---

## Características

* **Búsqueda automatizada** de autos en Mercado Libre Argentina, guardando la evolución de precios día a día.
* **UI web con Bootstrap y DataTables**: cómoda, moderna, responsiva y potente para filtrar, buscar, ordenar y exportar datos.
* **Selección rápida** de términos de búsqueda anteriores.
* **Visualización del historial de precios** de cada publicación con gráficos interactivos.
* **Persistencia MongoDB** para consulta histórica.
* **Variables de entorno** fácilmente configurables mediante `.env` o entorno del sistema.
* **Gestión de dependencias y ejecución con Astral UV**.

---

## Requisitos

* **Python 3.8+**
* **MongoDB** (local o remoto)
* **Astral UV** como gestor de paquetes y ejecución ([ver sitio oficial](https://astral.sh/uv/))

---

## Instalación

1. **Instala Astral UV**
   Si aún no tienes UV, instálalo siguiendo las [instrucciones oficiales](https://astral.sh/uv/)

2. **Clona el repositorio**:

   ```bash
   git clone https://github.com/tuusuario/mercadolibre-car-scraper.git
   cd mercadolibre-car-scraper
   ```

3. **Instala las dependencias del proyecto usando UV:**

   ```bash
   uv pip install -r requirements.txt
   ```

   UV gestiona el entorno virtual y las dependencias automáticamente (no es necesario crear ni activar un venv manual).

4. **Configura las variables de entorno**
   Crea un archivo `.env` en la raíz del proyecto con, por ejemplo:

   ```env
    MONGO_URI=mongodb://localhost:27017/
    MONGO_DB=ml
    MONGO_COLLECTION=cars
    DEBUG=False
    PORT=52021
   ```

   También puedes exportar estas variables en tu entorno de sistema.

---

## Uso

1. **Arranca la aplicación usando UV:**

   ```bash
   uv run main.py
   ```

2. **Accede a la aplicación desde tu navegador:**

   ```
   http://localhost:52021/
   ```

3. **Busca autos, consulta históricos y exporta datos:**

   * Usa el input de búsqueda o selecciona búsquedas anteriores.
   * Visualiza resultados con imágenes, precios, ubicaciones y variaciones.
   * Haz clic en “Ver evolución” para abrir el gráfico histórico de precios.
   * Descarga los datos en CSV cuando lo desees.

---

## Variables de entorno soportadas

* `MONGO_URI`: Cadena de conexión a tu instancia de MongoDB (ejemplo: `mongodb://localhost:27017/`)
* `MONGO_DB`: Nombre de la database mongodb
* `MONGO_COLLECTION`: Nombre de la collection donde se almacenan los datos
* `PORT`: Puerto en el que corre la aplicación Flask (ejemplo: `52021`)
* `DEBUG`: Modo debug de Flask (`True` o `False`)

---

## Notas técnicas

* **Astral UV** gestiona de manera eficiente y reproducible todas las dependencias y su ejecución, integrando entorno virtual.
* Los datos de cada búsqueda y su evolución diaria quedan almacenados en MongoDB, facilitando análisis históricos.
* El frontend usa Bootstrap 5 y DataTables (ambos vía CDN) para una experiencia de usuario fluida y moderna.
* El proyecto está listo para ser desplegado tanto localmente como en servidores en la nube.

---

## Dependencias principales

* Flask
* requests
* beautifulsoup4
* pandas
* pymongo
* python-dotenv
* DataTables (por CDN en la plantilla)
* Bootstrap (por CDN en la plantilla)

---

## Ejemplo de archivo `.env`

```env
MONGO_URI=mongodb://localhost:27017/
PORT=52021
DEBUG=False
```

---

## Licencia

MIT
