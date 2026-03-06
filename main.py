# main.py
import os
import re
import shutil
import zipfile
from html import unescape
from xml.etree import ElementTree as ET

from kivy.app import App
from kivy.clock import Clock
from kivy.logger import Logger
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

# =========================
# CONFIG
# =========================
APP_COPY_NAME = "libro.epub"
ANDROID_FILE_REQ_CODE = 1001


# =========================
# UTILIDADES EPUB
# =========================
def strip_html_to_text(html_bytes: bytes) -> str:
    """Convierte HTML/XHTML a texto plano de forma sencilla y robusta."""
    try:
        text = html_bytes.decode("utf-8", errors="ignore")
    except Exception:
        text = str(html_bytes)

    # Quitar scripts/styles
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)

    # Saltos de bloque razonables
    text = re.sub(r"(?i)</(p|div|h1|h2|h3|h4|h5|h6|li|section|article|tr|br)>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)

    # Quitar el resto de etiquetas
    text = re.sub(r"(?s)<[^>]+>", " ", text)

    # Entidades HTML
    text = unescape(text)

    # Limpiar espacios
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def extract_epub_text(epub_path: str, max_chars: int = 12000) -> str:
    """
    Extrae texto legible de un EPUB siguiendo container.xml -> OPF -> spine.
    Sin dependencias externas.
    """
    if not os.path.exists(epub_path):
        return "No existe el archivo EPUB."

    try:
        with zipfile.ZipFile(epub_path, "r") as zf:
            names = set(zf.namelist())

            # 1) Localizar OPF desde META-INF/container.xml
            if "META-INF/container.xml" not in names:
                return "EPUB no válido: falta META-INF/container.xml"

            container_xml = zf.read("META-INF/container.xml")
            container_root = ET.fromstring(container_xml)

            rootfile_elem = container_root.find(
                ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
            )
            if rootfile_elem is None:
                return "EPUB no válido: no se encontró el OPF."

            opf_path = rootfile_elem.attrib.get("full-path", "").strip()
            if not opf_path or opf_path not in names:
                return "EPUB no válido: ruta OPF incorrecta."

            opf_dir = os.path.dirname(opf_path)
            opf_xml = zf.read(opf_path)
            opf_root = ET.fromstring(opf_xml)

            # Namespace OPF
            ns = {"opf": "http://www.idpf.org/2007/opf"}

            # 2) Manifest: id -> href
            manifest = {}
            for item in opf_root.findall(".//opf:manifest/opf:item", ns):
                item_id = item.attrib.get("id", "").strip()
                href = item.attrib.get("href", "").strip()
                media_type = item.attrib.get("media-type", "").strip()
                if item_id and href:
                    manifest[item_id] = (href, media_type)

            # 3) Spine: orden de lectura
            spine_ids = []
            for itemref in opf_root.findall(".//opf:spine/opf:itemref", ns):
                idref = itemref.attrib.get("idref", "").strip()
                if idref:
                    spine_ids.append(idref)

            if not spine_ids:
                return "No se encontró el orden de lectura del EPUB."

            collected = []

            # 4) Leer documentos en orden
            for idref in spine_ids:
                item = manifest.get(idref)
                if not item:
                    continue

                href, media_type = item
                if media_type not in (
                    "application/xhtml+xml",
                    "text/html",
                    "application/xml",
                ):
                    continue

                full_path = os.path.normpath(os.path.join(opf_dir, href)).replace("\\", "/")
                if full_path not in names:
                    continue

                raw = zf.read(full_path)
                text = strip_html_to_text(raw)

                if text:
                    collected.append(text)

                joined = "\n\n".join(collected).strip()
                if len(joined) >= max_chars:
                    return joined[:max_chars].strip()

            final_text = "\n\n".join(collected).strip()
            if not final_text:
                return "No se pudo extraer texto legible del EPUB."

            return final_text[:max_chars].strip()

    except zipfile.BadZipFile:
        return "El archivo no es un EPUB válido o está dañado."
    except Exception as e:
        Logger.exception("Error extrayendo EPUB")
        return f"Error leyendo EPUB: {e}"


# =========================
# APP
# =========================
class AudioLibroApp(App):
    def build(self):
        self.title = "AudioLibro"
        self.selected_uri = None
        self.local_epub_path = None

        root = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))

        self.info_label = Label(
            text="Pulsa 'Elegir EPUB' para cargar un libro.",
            size_hint_y=None,
            height=dp(60),
            halign="left",
            valign="middle",
        )
        self.info_label.bind(size=self._update_label_text_size)

        btns = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(10))

        self.pick_btn = Button(text="Elegir EPUB")
        self.pick_btn.bind(on_release=self.pick_epub)

        self.preview_btn = Button(text="Mostrar texto")
        self.preview_btn.bind(on_release=self.show_epub_text)

        btns.add_widget(self.pick_btn)
        btns.add_widget(self.preview_btn)

        self.text_box = TextInput(
            text="Aquí aparecerá el texto extraído del EPUB.",
            readonly=True,
            multiline=True,
            font_size="16sp",
            size_hint_y=None,
        )
        self.text_box.bind(minimum_height=self.text_box.setter("height"))
        self.text_box.height = dp(1200)

        scroll = ScrollView()
        scroll.add_widget(self.text_box)

        root.add_widget(self.info_label)
        root.add_widget(btns)
        root.add_widget(scroll)

        return root

    def _update_label_text_size(self, instance, size):
        instance.text_size = (size[0], None)

    def app_storage_epub_path(self) -> str:
        return os.path.join(self.user_data_dir, APP_COPY_NAME)

    def set_status(self, message: str):
        Logger.info(f"APP: {message}")
        self.info_label.text = message

    # =========================
    # SELECCIÓN DE EPUB EN ANDROID
    # =========================
    def pick_epub(self, *_args):
        if self._is_android():
            self._pick_epub_android()
        else:
            self.set_status("Esta versión está pensada para Android.")
            self.text_box.text = "Prueba en Android para usar el selector de EPUB."

    def _is_android(self) -> bool:
        try:
            from kivy.utils import platform
            return platform == "android"
        except Exception:
            return False

    def _pick_epub_android(self):
        try:
            from jnius import autoclass, cast
            from android import activity

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Intent = autoclass("android.content.Intent")
            String = autoclass("java.lang.String")

            current_activity = PythonActivity.mActivity

            try:
                activity.unbind(on_activity_result=self._on_activity_result)
            except Exception:
                pass

            activity.bind(on_activity_result=self._on_activity_result)

            intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
            intent.addCategory(Intent.CATEGORY_OPENABLE)
            intent.setType("application/epub+zip")
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            intent.addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)

            chooser_title = cast("java.lang.CharSequence", String("Elegir EPUB"))
            chooser = Intent.createChooser(intent, chooser_title)

            current_activity.startActivityForResult(chooser, ANDROID_FILE_REQ_CODE)
            self.set_status("Abriendo selector de EPUB...")

        except Exception as e:
            Logger.exception("Error abriendo selector Android")
            self.set_status(f"Error al abrir selector: {e}")

    def _on_activity_result(self, request_code, result_code, intent):
        if request_code != ANDROID_FILE_REQ_CODE:
            return

        try:
            from jnius import autoclass, cast

            Activity = autoclass("android.app.Activity")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            current_activity = PythonActivity.mActivity

            if result_code != Activity.RESULT_OK or intent is None:
                self.set_status("Selección cancelada.")
                return

            uri = intent.getData()
            if uri is None:
                self.set_status("No se recibió ningún archivo.")
                return

            try:
                current_activity.getContentResolver().takePersistableUriPermission(
                    uri,
                    intent.getFlags() & (
                        autoclass("android.content.Intent").FLAG_GRANT_READ_URI_PERMISSION
                    )
                )
            except Exception:
                # No todos los proveedores lo permiten; no es crítico
                pass

            uri_str = str(uri)
            Logger.info(f"DEBUG native uri = {uri_str}")

            local_path = self._copy_uri_to_internal_file(uri)
            self.local_epub_path = local_path

            self.set_status(f"EPUB cargado:\n{local_path}")
            self.text_box.text = (
                f"EPUB cargado correctamente.\n\nRuta local:\n{local_path}\n\n"
                "Pulsa 'Mostrar texto' para extraer una vista previa."
            )

        except Exception as e:
            Logger.exception("Error procesando resultado del selector")
            self.set_status(f"Error al recibir EPUB: {e}")

    def _copy_uri_to_internal_file(self, uri):
        from jnius import autoclass
        import os

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        current_activity = PythonActivity.mActivity
        resolver = current_activity.getContentResolver()

        pfd = None
        src = None

        try:
            pfd = resolver.openFileDescriptor(uri, "r")
            if pfd is None:
                raise Exception("No se pudo abrir ParcelFileDescriptor del EPUB")

            # Desacoplamos el fd Java y lo usamos con Python
            fd = pfd.detachFd()

            base_dir = current_activity.getFilesDir().getAbsolutePath()
            dest_path = os.path.join(base_dir, "libro.epub")

            with os.fdopen(fd, "rb") as src, open(dest_path, "wb") as dst:
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)

            return dest_path

        finally:
            try:
                if pfd is not None:
                    pfd.close()
            except Exception:
                pass

    # =========================
    # TEXTO
    # =========================
    def show_epub_text(self, *_args):
        epub_path = self.app_storage_epub_path()

        if not os.path.exists(epub_path):
            self.set_status("Primero elige un EPUB.")
            self.text_box.text = "No hay ningún EPUB cargado todavía."
            return

        self.set_status("Extrayendo texto del EPUB...")
        Clock.schedule_once(lambda dt: self._do_extract_text(epub_path), 0.1)

    def _do_extract_text(self, epub_path: str):
        text = extract_epub_text(epub_path, max_chars=12000)

        if not text.strip():
            text = "No se pudo extraer texto."

        self.text_box.text = text
        self.set_status(f"Texto extraído desde:\n{epub_path}")


if __name__ == "__main__":
    AudioLibroApp().run()
