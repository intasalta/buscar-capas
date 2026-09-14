# 🌍 Buscador de Capas WMS – IDGEO INTA (GitHub Pages)

Esta carpeta contiene la versión web estática optimizada para alojarse en **GitHub Pages**.

## 🚀 Ventajas
- **100% Gratuito y Siempre Activo**: No se duerme ni se apaga como los servicios de contenedores.
- **Sin Bloqueo de IP**: Las búsquedas se hacen en el navegador del usuario en memoria usando `layers_cache.json`.
- **Ultra Rápido**: Búsquedas y filtros en menos de 50 milisegundos.

---

## 💻 Cómo Probarlo Localmente

Podés probar la página abriendo un servidor web local con Python:

```powershell
python -m http.server 8000 --directory HTMLWMS
```
Luego abrí en tu navegador: [http://localhost:8000](http://localhost:8000)

---

## 🌐 Cómo Publicarlo en GitHub Pages

1. Subí los cambios a tu repositorio de GitHub:
   ```powershell
   git add .
   git commit -m "feat: agregar versión web estática en HTMLWMS"
   git push
   ```
2. En tu repositorio en GitHub:
   - Andá a **Settings** (Configuración) > **Pages** (en el menú lateral izquierdo).
   - En **Build and deployment > Branch**:
     - Seleccioná tu rama principal (`main` o `master`).
     - En la carpeta seleccioná **/ (root)** si movés el index a la raíz, o podés usar GitHub Actions para publicar la carpeta `/HTMLWMS`.
3. ¡Listo! Tu página quedará publicada en:
   `https://<tu-usuario>.github.io/<tu-repositorio>/` (o `/HTMLWMS/`).

---

## 🔄 Actualización Automática

El repositorio incluye un workflow en `.github/workflows/update_layers.yml` que:
- Se ejecuta automáticamente todos los domingos.
- Consulta los 16 nodos de INTA desde los servidores de GitHub (evitando usar tu IP de casa).
- Actualiza `layers_cache.json` y hace commit automáticamente.
