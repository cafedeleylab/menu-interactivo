# Actualizar en GitHub y publicar automáticamente en Vercel

Tu menú público es: https://menu-interactivo-cdl.vercel.app/

El flujo se mantiene: **subes una actualización al mismo repositorio de GitHub y Vercel despliega esa actualización automáticamente**. La configuración de la base de datos y de la clave se hace una sola vez en el proyecto de Vercel.

## 1. Subir los archivos sueltos al repositorio existente

Usa **todos los archivos de `para-vercel/`**. Están sueltos, sin subcarpetas: el menú comprimido, sus imágenes, la API y la configuración.

En GitHub, entra al repositorio que ya está conectado a Vercel:

1. Abre **Add file → Upload files**.
2. Abre `para-vercel` en Finder, selecciona todos sus archivos y arrástralos al área de carga de GitHub.
3. Confirma con **Commit changes** en la rama que Vercel usa como producción.

En la raíz deben quedar `index.html`, `api.py`, `cdl_backend.py`, `cdl_storage.py`, `cdl_catalog.json`, `requirements.txt`, `vercel.json` y las imágenes. No cargues la carpeta contenedora `para-vercel`: carga sus archivos. No necesitas crear carpetas `api` ni `server`.

## 2. Revisar una vez el proyecto de Vercel

En el proyecto del menú:

- En **Settings → Git**, conserva la conexión con el repositorio existente y su rama de producción.
- En **Settings → Build and Deployment**, usa el directorio donde colocaste `vercel.json` como Root Directory. Si subiste el contenido a la raíz del repositorio, déjalo en la raíz.
- El archivo `vercel.json` ya indica qué archivos publicar y cómo ejecutar la API Python desde `api.py`. La entrega usa archivos sueltos; no requiere un comando de compilación manual.
- Si antes configuraste manualmente Build Command u Output Directory, desactiva esos overrides y deja que se aplique `vercel.json`. Usa Framework Preset **Other**.

El sitio publica solo los archivos del menú. El código de servidor se ejecuta como una función; no se publica como archivo descargable.

## 3. Conectar la base de datos una sola vez

Abre el proyecto en Vercel y entra en **Storage**. Elige crear o conectar una base de datos del Marketplace y selecciona **Neon / Postgres**. Conecta la base al proyecto `menu-interactivo-cdl` y habilita su uso en el entorno **Production**.

La integración debe agregar una variable de entorno llamada `DATABASE_URL` con la conexión a Postgres. Si ofrece una conexión pooled y otra directa, usa la pooled para la API. No copies esa dirección a GitHub ni a `order-config.js`.

Los pedidos, sesiones y cuentas se guardarán en esa base. El servidor crea sus tablas automáticamente al recibir la primera petición. No es necesario ejecutar SQL manualmente.

La base permanece al volver a desplegar el código: no se guarda en archivos temporales de Vercel.

## 4. Configurar la clave del equipo una sola vez

En **Settings → Environment Variables**, crea estas variables para **Production**:

| Nombre | Valor |
| --- | --- |
| `CAFE_ALLOWED_ORIGINS` | `https://menu-interactivo-cdl.vercel.app` |
| `CAFE_TEAM_PASSWORD_HASH` | El hash privado que se preparó en tu equipo. |

El valor del hash está en el archivo local **`server/data/vercel-secrets.env`**, en la carpeta editable del proyecto. Copia solo lo que aparece después de `CAFE_TEAM_PASSWORD_HASH=` y pégalo como secreto en Vercel. Ese archivo está excluido de Git y no está incluido en `para-vercel/` ni en su ZIP. No subas la carpeta `server/data`.

La clave indicada por ti ya está configurada en ese hash. No necesitas escribir la clave en JavaScript ni en el repositorio.

Si necesitas generar otro hash más adelante, en la carpeta editable ejecuta `python3 server/app.py --hash` e introduce la clave cuando la terminal la solicite.

## 5. Publicar esta configuración inicial

Después de conectar Postgres y guardar las variables, entra en **Deployments** y vuelve a desplegar la versión más reciente con **Redeploy** para que tome la configuración.

Abre:

https://menu-interactivo-cdl.vercel.app/api/health

El resultado correcto incluye:

```json
{"ok": true, "team_configured": true, "database": "postgres"}
```

Si indica que falta conectar Postgres, revisa `DATABASE_URL` y que esté disponible en Production. Si `team_configured` es `false`, revisa `CAFE_TEAM_PASSWORD_HASH`. Si aparece una página HTML en vez de JSON, revisa que subiste `api.py`, `cdl_backend.py`, `cdl_storage.py`, `cdl_catalog.json`, `requirements.txt` y `vercel.json` al directorio raíz correcto y volviste a desplegar.

Prueba un pedido en un celular y abre **Team Work** en otros dos: recibe el QR, vincúlalo a una cuenta y comprueba que ambos ven el mismo estado.

## 6. Siguientes actualizaciones

Solo actualiza los archivos en **ese mismo repositorio y rama**. Vercel volverá a desplegar automáticamente. Conserva las variables y la base ya conectadas.

Si sigues editando las fuentes locales, regenera las carpetas antes de subir:

```sh
python3 generar-github.py
python3 generar-vercel.py
```

Sube el contenido actualizado de `para-vercel/`. El archivo suelto `cdl_catalog.json` contiene los precios validados por el servidor y debe coincidir con el menú. En las fuentes editables corresponde a `server/catalog.json`; el generador lo copia automáticamente.

## Estado de esta entrega

El código y el paquete están preparados para Vercel. No se ha creado una base en tu cuenta ni modificado la página publicada. La conexión de Neon y las variables iniciales se completan desde tu panel.

## Referencias oficiales

- [Despliegues desde GitHub](https://vercel.com/docs/deployments/git/vercel-for-github)
- [Bases de datos desde el Marketplace de Vercel](https://vercel.com/docs/storage)
- [Funciones Python](https://vercel.com/docs/functions/runtimes/python)
- [Configuración de Vercel](https://vercel.com/docs/project-configuration/vercel-json)
