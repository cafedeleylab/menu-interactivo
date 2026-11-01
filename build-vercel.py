"""Publica solo el menú; código privado, claves y base nunca son archivos estáticos."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / 'public'
# La carpeta para subir a GitHub ya lleva el código y las imágenes comprimidos.
# Si se trabaja directamente con las fuentes, se publica dist sin comprimir.
SOURCE = ROOT / 'archivos-sueltos-github'
if not (SOURCE / 'index.html').is_file():
    SOURCE = ROOT / 'dist'
if not (SOURCE / 'index.html').is_file():
    SOURCE = ROOT
if PUBLIC.exists():
    shutil.rmtree(PUBLIC)
if SOURCE == ROOT:
    PUBLIC.mkdir()
    for item in SOURCE.iterdir():
        if item.is_file() and item.suffix.lower() in {'.html','.js','.webp','.png','.jpg','.svg','.txt'}:
            if item.name != 'requirements.txt':
                shutil.copy2(item, PUBLIC / item.name)
else:
    shutil.copytree(SOURCE, PUBLIC)
(PUBLIC / 'order-config.js').write_text("window.CafeOrderConfig = { apiBase: window.location.origin };\n")
print('Menú público preparado; la API vive en /api dentro del mismo dominio.')
