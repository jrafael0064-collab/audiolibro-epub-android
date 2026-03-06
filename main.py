import sys
import traceback

def _install_crash_logger():
    def excepthook(exc_type, exc, tb):
        txt = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            from android.storage import app_storage_path
            base = app_storage_path()
            path = base + "/crash.log"
            with open(path, "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception:
            pass
        try:
            import android
            android.logger.error(txt)
        except Exception:
            pass

    sys.excepthook = excepthook

_install_crash_logger()

import json
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
import os
import shutil
import time
import shutil
from urllib.parse import urlparse

from kivy.app import App
from kivy.lang import Builder
from kivy.clock import Clock
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.properties import StringProperty, ListProperty, NumericProperty, BooleanProperty
from pathlib import Path
from kivy.clock import Clock

# plyer puede fallar en algunas ROMs; lo usamos pero con fallback
try:
    from plyer import filechooser
except Exception:
    filechooser = None


KV = r"""
#:import dp kivy.metrics.dp

<SetupScreen>:
    BoxLayout:
        orientation: "vertical"
        padding: dp(12)
        spacing: dp(10)

        Label:
            text: "📖 Audiolibro EPUB (personal)"
            bold: True
            size_hint_y: None
            height: self.texture_size[1] + dp(8)

        Label:
            text: root.status_text
            size_hint_y: None
            height: self.texture_size[1] + dp(6)

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(8)

            Button:
                text: "Elegir EPUB"
                on_release: app.pick_epub()

            Label:
                text: app.selected_epub_name or "Ninguno"
                halign: "left"
                valign: "middle"
                text_size: self.size

        Label:
            text: "Voz"
            size_hint_y: None
            height: self.texture_size[1] + dp(6)

        Spinner:
            text: app.voice_selected or "Sistema (es-ES)"
            values: app.voice_names
            disabled: not app.voices_ready
            size_hint_y: None
            height: dp(44)
            on_text: app.set_voice(self.text)

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(8)

            Label:
                text: "Velocidad"
                size_hint_x: None
                width: dp(90)

            Slider:
                min: 0.5
                max: 2.0
                value: app.rate
                on_value: app.set_rate(self.value)

            Label:
                text: "{:.2f}x".format(app.rate)
                size_hint_x: None
                width: dp(60)

        Button:
            text: "Empezar"
            size_hint_y: None
            height: dp(48)
            disabled: not app.can_start
            on_release: app.start_player()

        Label:
            text: "Tip: Ajustes Android → Texto a voz → instala Español (Google TTS)."
            font_size: "12sp"
            opacity: 0.8

<PlayerScreen>:
    BoxLayout:
        orientation: "vertical"
        padding: dp(12)
        spacing: dp(10)

        Label:
            text: app.book_title or "Sin libro"
            bold: True
            size_hint_y: None
            height: self.texture_size[1] + dp(6)

        Label:
            text: app.player_status
            size_hint_y: None
            height: self.texture_size[1] + dp(6)

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(8)

            Button:
                text: "⟵ Setup"
                on_release: app.go_setup()

            Button:
                text: "▶ Play"
                on_release: app.play()

            Button:
                text: "⏸ Pausa"
                on_release: app.pause()

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(8)

            Button:
                text: "⏮ Anterior"
                on_release: app.prev_chapter()

            Button:
                text: "⏭ Siguiente"
                on_release: app.next_chapter()

            Button:
                text: "↩ Reanudar"
                on_release: app.resume()

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(8)

            Label:
                text: "Velocidad"
                size_hint_x: None
                width: dp(90)

            Slider:
                min: 0.5
                max: 2.0
                value: app.rate
                on_value: app.set_rate(self.value)

            Label:
                text: "{:.2f}x".format(app.rate)
                size_hint_x: None
                width: dp(60)

        Label:
            text: "Vista previa (capítulo actual):"
            size_hint_y: None
            height: self.texture_size[1] + dp(4)

        TextInput:
            text: app.preview_text
            readonly: True
            multiline: True
"""


# ---------------------------
# EPUB parsing (stdlib-only)
# ---------------------------
class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data:
            self.parts.append(data)

    def get_text(self):
        return " ".join(self.parts)


def html_to_text(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html)
    text = s.get_text()
    return " ".join(text.split())


def _find_opf_path(z: zipfile.ZipFile) -> str:
    container = z.read("META-INF/container.xml")
    root = ET.fromstring(container)
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfiles = root.findall(".//c:rootfile", ns)
    if not rootfiles:
        rootfiles = root.findall(".//rootfile")
    for rf in rootfiles:
        fp = rf.attrib.get("full-path")
        if fp:
            return fp
    raise RuntimeError("No encuentro content.opf en container.xml")


def epub_to_chapters(epub_path: str):
    with zipfile.ZipFile(epub_path, "r") as z:
        opf_path = _find_opf_path(z)
        opf_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""

        opf_root = ET.fromstring(z.read(opf_path))

        ns = {
            "opf": "http://www.idpf.org/2007/opf",
            "dc": "http://purl.org/dc/elements/1.1/",
        }

        title = "EPUB"
        t = opf_root.find(".//dc:title", ns)
        if t is not None and (t.text or "").strip():
            title = t.text.strip()

        manifest = {}
        for item in opf_root.findall(".//opf:manifest/opf:item", ns):
            _id = item.attrib.get("id")
            href = item.attrib.get("href")
            if _id and href:
                manifest[_id] = href

        spine_ids = []
        for itemref in opf_root.findall(".//opf:spine/opf:itemref", ns):
            rid = itemref.attrib.get("idref")
            if rid:
                spine_ids.append(rid)

        chapters = []
        for rid in spine_ids:
            href = manifest.get(rid)
            if not href:
                continue
            internal_path = f"{opf_dir}/{href}" if opf_dir else href
            internal_path = internal_path.replace("//", "/")
            try:
                raw = z.read(internal_path).decode("utf-8", errors="ignore")
            except KeyError:
                continue

            txt = html_to_text(raw)
            if len(txt) > 400:
                chapters.append(txt)

        return title, chapters


def chunk_text(text: str, max_len: int = 900):
    words = text.replace("\n", " ").split()
    out, buf, cur = [], [], 0
    for w in words:
        if cur + len(w) + 1 > max_len and buf:
            out.append(" ".join(buf).strip())
            buf, cur = [], 0
        buf.append(w)
        cur += len(w) + 1
    if buf:
        out.append(" ".join(buf).strip())
    return out


# ---------------------------
# Android TTS via PyJNIus (bajo demanda)
# ---------------------------
class AndroidTTS:
    def __init__(self):
        self.tts = None
        self.ready = False
        self.rate = 1.0
        self.last_lang_status = None
        self.last_engine = "desconocido"
        self.last_speak_result = None
        self._init()

    def _init(self):
        self.ready = False
        self.tts = None
        self.last_lang_status = None
        self.last_engine = "desconocido"
        self.last_speak_result = None

        try:
            from jnius import autoclass
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            Locale = autoclass("java.util.Locale")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity

            self.tts = TextToSpeech(activity, None)

            try:
                eng = self.tts.getDefaultEngine()
                if eng:
                    self.last_engine = str(eng)
            except Exception:
                pass

            try:
                candidates = [
                    Locale("es", "ES"),
                    Locale("es", ""),
                ]
                for loc in candidates:
                    try:
                        st = int(self.tts.setLanguage(loc))
                        self.last_lang_status = st
                        if st >= 0:
                            break
                    except Exception:
                        continue
            except Exception:
                self.last_lang_status = None

            try:
                self.tts.setSpeechRate(float(self.rate))
            except Exception:
                pass

            self.ready = True

        except Exception:
            self.tts = None
            self.ready = False
            self.last_lang_status = None
            self.last_engine = "desconocido"

    def set_rate(self, r: float):
        self.rate = max(0.5, min(2.0, float(r)))
        if self.tts and self.ready:
            try:
                self.tts.setSpeechRate(self.rate)
            except Exception:
                pass

    def speak(self, text: str) -> bool:
        if not self.tts or not self.ready:
            self.last_speak_result = "tts_not_ready"
            return False
        try:
            from jnius import autoclass
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            Bundle = autoclass("android.os.Bundle")

            params = Bundle()
            params.putString("utteranceId", "utt1")

            res = int(self.tts.speak(text, TextToSpeech.QUEUE_FLUSH, params, "utt1"))
            self.last_speak_result = res
            return res == int(TextToSpeech.SUCCESS)
        except Exception as e:
            self.last_speak_result = f"exception: {e}"
            return False

    def stop(self):
        if self.tts and self.ready:
            try:
                self.tts.stop()
            except Exception:
                pass

    def shutdown(self):
        if self.tts:
            try:
                self.tts.shutdown()
            except Exception:
                pass
        self.tts = None
        self.ready = False

# ---------------------------
# Screens
# ---------------------------
class SetupScreen(Screen):
    status_text = StringProperty("Listo. Elige EPUB y pulsa Empezar.")


class PlayerScreen(Screen):
    pass


# ---------------------------
# App
# ---------------------------
class AudioLibroApp(App):
    FILE_REQ_CODE = 4242
    voice_names = ListProperty(["Sistema (es-ES)"])
    voices_ready = BooleanProperty(True)
    voice_selected = StringProperty("Sistema (es-ES)")
    rate = NumericProperty(1.0)

    selected_epub_path = StringProperty("")
    selected_epub_name = StringProperty("")
    can_start = BooleanProperty(False)

    book_title = StringProperty("")
    player_status = StringProperty("Carga un EPUB en Setup")
    preview_text = StringProperty("")

    def build(self):
        Builder.load_string(KV)

        self.sm = ScreenManager()
        self.setup = SetupScreen(name="setup")
        self.player = PlayerScreen(name="player")
        self.sm.add_widget(self.setup)
        self.sm.add_widget(self.player)

        self.tts = None

        self.chapters = []
        self.chapter_idx = 0
        self.chunks = []
        self.chunk_idx = 0
        self.playing = False

        self._load_settings()
        self._refresh_can_start()
        return self.sm

    # ---------- helpers ----------
    def _on_picker_cancelled(self):
        self.setup.status_text = "Selección cancelada."

    def _set_status_error(self, msg):
        self.setup.status_text = msg    
    
    def _ensure_tts(self) -> bool:
        if self.tts is None:
            self.tts = AndroidTTS()
            if self.tts and self.tts.ready:
                self.tts.set_rate(self.rate)
        return bool(self.tts and self.tts.ready)

    def _settings_file(self):
        return Path(self.user_data_dir) / "settings.json"

    def _progress_file(self):
        return Path(self.user_data_dir) / "progress.json"

    def _load_settings(self):
        try:
            p = self._settings_file()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                self.rate = float(data.get("rate", 1.0))
                self.voice_selected = str(data.get("voice", "Sistema (es-ES)")) or "Sistema (es-ES)"
        except Exception:
            pass

    def _save_settings(self):
        try:
            data = {"rate": float(self.rate), "voice": self.voice_selected}
            self._settings_file().write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _save_progress(self):
        try:
            data = {
                "epub_path": self.selected_epub_path,
                "chapter_idx": self.chapter_idx,
                "chunk_idx": self.chunk_idx,
            }
            self._progress_file().write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _load_progress(self):
        try:
            p = self._progress_file()
            if not p.exists():
                return None
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def set_voice(self, voice_name: str):
        if not voice_name:
            return
        self.voice_selected = voice_name
        self._save_settings()

    def set_rate(self, r: float):
        self.rate = float(r)
        if self.tts and self.tts.ready:
            self.tts.set_rate(self.rate)
        self._save_settings()

    def _set_epub_selected(self, path: str):
        if path is None:
            self.setup.status_text = "Ruta EPUB no válida."
            return

        path = str(path).strip()
        if not path:
            self.setup.status_text = "Ruta EPUB vacía."
            return

        if path.startswith("content://"):
            local_path = self._copy_content_uri_to_local(path)
            if not local_path:
                self.setup.status_text = "No pude importar el EPUB desde Android."
                return
            path = local_path

        self.selected_epub_path = path
        self.selected_epub_name = Path(path).name
        self.setup.status_text = "EPUB seleccionado. Pulsa Empezar."
        self._refresh_can_start()

    def _refresh_can_start(self):
        self.can_start = bool(self.selected_epub_path)

    def _is_content_uri(self, value: str) -> bool:
        try:
            return isinstance(value, str) and value.startswith("content://")
        except Exception:
            return False

    def _copy_content_uri_to_local(self, uri: str):
        try:
            from jnius import autoclass

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Uri = autoclass("android.net.Uri")
            OpenableColumns = autoclass("android.provider.OpenableColumns")
            FileOutputStream = autoclass("java.io.FileOutputStream")
            ByteArray = autoclass("java.lang.reflect.Array")
            Byte = autoclass("java.lang.Byte")

            activity = PythonActivity.mActivity
            resolver = activity.getContentResolver()
            parsed = Uri.parse(uri)

            stream = resolver.openInputStream(parsed)
            if stream is None:
                print("openInputStream devolvió None")
                return None

            filename = "libro.epub"

            try:
                cursor = resolver.query(parsed, None, None, None, None)
                if cursor:
                    idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if idx >= 0 and cursor.moveToFirst():
                        name = cursor.getString(idx)
                        if name:
                            filename = str(name)
                    cursor.close()
            except Exception as e:
                print("No se pudo leer DISPLAY_NAME:", e)

            if not filename.lower().endswith(".epub"):
                filename += ".epub"

            safe_name = filename.replace("/", "_").replace("\\", "_")
            dest = Path(self.user_data_dir) / safe_name

            fos = FileOutputStream(str(dest))

            buf = ByteArray.newInstance(Byte.TYPE, 8192)

            while True:
                read = stream.read(buf)
                if read == -1:
                    break
                fos.write(buf, 0, read)

            fos.flush()
            fos.close()
            stream.close()

            print("DEBUG local epub =", str(dest))
            print("DEBUG 1 local_path =", local_path)
            self.book_path = local_path
            print("DEBUG 2 book_path asignado")
            print("DEBUG 3 existe =", os.path.exists(local_path))
            print("DEBUG 4 tamaño =", os.path.getsize(local_path))
            self.cargar_epub(local_path)
            print("DEBUG 5 cargar_epub terminado")
            return str(dest)

        except Exception as e:
            print("copy_content_uri_to_local error:", e)
            return None

    # ---------- EPUB picker (Plyer + fallback nativo) ----------
    def pick_epub(self):
        print("DEBUG cargar_epub entrada =", path)
        try:
            from jnius import autoclass, cast

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Intent = autoclass("android.content.Intent")
            String = autoclass("java.lang.String")

            activity = PythonActivity.mActivity

            intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
            intent.addCategory(Intent.CATEGORY_OPENABLE)

            # Primero intentamos EPUB
            intent.setType("application/epub+zip")

            # Permisos persistentes sobre el documento
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            intent.addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)

            chooser = Intent.createChooser(intent, cast("java.lang.CharSequence", String("Seleccionar EPUB")))
            activity.startActivityForResult(chooser, self.FILE_REQ_CODE)

            self.setup.status_text = "Abriendo selector de EPUB..."
        except Exception as e:
            self.setup.status_text = f"Error abriendo selector nativo: {e}"
            print("pick_epub error:", e)

    def _fallback_if_not_selected(self, *_):
        # Si en ~1.2s sigue vacío, es que el callback no llegó → abrir fallback
        # (si el usuario aún está en el selector, esto no molesta: el intent nativo se abre al volver)
        if not self.selected_epub_path:
            self._open_document_fallback()
        
    def _open_document_fallback(self):
        # Fallback nativo con Intent.ACTION_OPEN_DOCUMENT
        try:
            from jnius import autoclass, cast
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")

            activity = PythonActivity.mActivity

            intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
            intent.addCategory(Intent.CATEGORY_OPENABLE)
            intent.setType("*/*")
            # Filtramos a epub si el gestor lo soporta
            # (algunos solo respetan MIME types conocidos)
            try:
                intent.putExtra(Intent.EXTRA_MIME_TYPES, ["application/epub+zip", "application/octet-stream"])
            except Exception:
                pass

            # Guardamos un callback temporal en la app
            # Kivy/pyjnius: usamos on_activity_result hook
            self._pending_pick = True

            REQUEST_CODE = 43210
            self._DOC_REQUEST_CODE = REQUEST_CODE

            activity.startActivityForResult(intent, REQUEST_CODE)
            self.setup.status_text = "Selecciona un EPUB (fallback nativo)..."
        except Exception as e:
            self.setup.status_text = f"No puedo abrir selector (fallback): {e}"

    def on_start(self):
        # Hook para Activity result
        try:
            from android import activity
            activity.bind(on_activity_result=self._on_activity_result)
        except Exception as e:
            print("No se pudo bindear on_activity_result:", e)

    def _on_activity_result(self, request_code, result_code, intent):
        try:
            if request_code != self.FILE_REQ_CODE:
                return

            # RESULT_OK = -1
            if result_code != -1 or intent is None:
                Clock.schedule_once(lambda dt: self._on_picker_cancelled(), 0)
                return

            data = intent.getData()
            if data is None:
                Clock.schedule_once(lambda dt: self._on_picker_cancelled(), 0)
                return

            uri = str(data.toString())
            print("DEBUG native uri =", uri)

            # Intentar persistir permiso
            try:
                from jnius import autoclass
                PythonActivity = autoclass("org.kivy.android.PythonActivity")
                activity = PythonActivity.mActivity

                take_flags = (
                    intent.getFlags()
                    & (
                        autoclass("android.content.Intent").FLAG_GRANT_READ_URI_PERMISSION
                        | autoclass("android.content.Intent").FLAG_GRANT_WRITE_URI_PERMISSION
                    )
                )
                activity.getContentResolver().takePersistableUriPermission(data, take_flags)
            except Exception as e:
                print("No se pudo persistir permiso:", e)

            Clock.schedule_once(lambda dt: self._set_epub_selected(uri), 0)

        except Exception as e:
            print("on_activity_result error:", e)
            Clock.schedule_once(lambda dt: self._set_status_error(f"Error recibiendo EPUB: {e}"), 0)

    def _import_content_uri_to_local(self, uri_str: str):
        # Copia content://... a un fichero local dentro de user_data_dir
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            cr = activity.getContentResolver()

            Uri = autoclass("android.net.Uri")
            uri = Uri.parse(uri_str)

            ins = cr.openInputStream(uri)
            if ins is None:
                self.setup.status_text = "No puedo abrir el archivo seleccionado."
                return

            # Nombre destino
            dest_dir = Path(self.user_data_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / f"selected_{int(time.time())}.epub"

            # Copiar stream → fichero
            with open(dest_path, "wb") as out:
                buf = bytearray(1024 * 64)
                while True:
                    n = ins.read(buf)
                    if n is None or n <= 0:
                        break
                    out.write(bytes(buf[:n]))
            try:
                ins.close()
            except Exception:
                pass

            self._set_epub_selected(str(dest_path))
        except Exception as e:
            self.setup.status_text = f"Error importando EPUB: {e}"

    # ---------- player ----------
    def start_player(self):
        self.player_status = "Leyendo EPUB..."
        if self.tts and self.tts.ready:
            self.tts.stop()
        self.playing = False

        try:
            title, chapters = epub_to_chapters(self.selected_epub_path)
        except Exception as e:
            self.player_status = f"Error leyendo EPUB: {e}"
            self.sm.current = "player"
            return

        if not chapters:
            self.player_status = "No encontré capítulos útiles en ese EPUB."
            self.sm.current = "player"
            return

        self.book_title = title
        self.chapters = chapters
        self.chapter_idx = 0
        self.chunk_idx = 0
        self._load_current_chunks()

        prev = self._load_progress()
        if prev and prev.get("epub_path") == self.selected_epub_path:
            self.chapter_idx = int(prev.get("chapter_idx", 0))
            self.chunk_idx = int(prev.get("chunk_idx", 0))
            self._load_current_chunks()
            self.player_status = f"Progreso cargado · Cap {self.chapter_idx+1} · Parte {self.chunk_idx+1}"
        else:
            self.player_status = f"Capítulos: {len(self.chapters)} · En {self.chapter_idx+1}"

        self.preview_text = self._current_text()[:2000]
        self._save_progress()
        self.sm.current = "player"

    def go_setup(self):
        self.pause()
        self.sm.current = "setup"

    def _current_text(self):
        return self.chapters[self.chapter_idx] if self.chapters else ""

    def _speak_current_chunk(self):
        if not self.chapters:
            self.player_status = "Sin EPUB."
            return

        if not self._ensure_tts():
            self.player_status = "TTS no disponible."
            self.playing = False
            return

        self.tts.set_rate(self.rate)

        if not self.chunks:
            self._load_current_chunks()
        if not self.chunks:
            self.player_status = "Capítulo vacío."
            return

        texto = self.chunks[self.chunk_idx]

        def attempt(n):
            ok = self.tts.speak(texto)
            if ok:
                self.playing = True
                self._save_progress()
                self.player_status = (
                    f"▶ Cap {self.chapter_idx+1}/{len(self.chapters)} · "
                    f"Parte {self.chunk_idx+1}/{len(self.chunks)} · "
                    f"Motor: {self.tts.last_engine}"
                )
                return

            if n > 0:
                self.player_status = (
                    f"Preparando TTS... reintento {4-n}/3 · "
                    f"lang={self.tts.last_lang_status} · "
                    f"speak={self.tts.last_speak_result} · "
                    f"motor={self.tts.last_engine}"
                )
                Clock.schedule_once(lambda *_: attempt(n - 1), 1.0)
            else:
                self.playing = False
                self.player_status = (
                    f"TTS no responde · "
                    f"lang={self.tts.last_lang_status} · "
                    f"speak={self.tts.last_speak_result} · "
                    f"motor={self.tts.last_engine}"
                )

        attempt(3)

        self.playing = True
        self._save_progress()
        self.player_status = f"▶ Cap {self.chapter_idx+1}/{len(self.chapters)} · Parte {self.chunk_idx+1}/{len(self.chunks)}"

    def play(self):
        if not self._ensure_tts():
            self.player_status = "TTS no disponible."
            return

        self.tts.set_rate(self.rate)

        # prueba corta primero
        ok = self.tts.speak("Prueba de voz")
        if not ok:
            self.player_status = (
                f"TTS no responde · "
                f"lang={self.tts.last_lang_status} · "
                f"speak={self.tts.last_speak_result} · "
                f"motor={self.tts.last_engine}"
            )
            return

        Clock.schedule_once(lambda *_: self._speak_current_chunk(), 1.2)

    def pause(self):
        if self.tts and self.tts.ready:
            self.tts.stop()
        self.playing = False
        self._save_progress()
        self.player_status = "⏸ Pausado"

    def resume(self):
        self._speak_current_chunk()

    def next_chapter(self):
        if not self.chapters:
            return
        if self.tts and self.tts.ready:
            self.tts.stop()
        self.chapter_idx = min(self.chapter_idx + 1, len(self.chapters) - 1)
        self.chunk_idx = 0
        self._load_current_chunks()
        self.preview_text = self._current_text()[:2000]
        self._save_progress()
        self.player_status = f"Cap {self.chapter_idx+1}/{len(self.chapters)}"

    def prev_chapter(self):
        if not self.chapters:
            return
        if self.tts and self.tts.ready:
            self.tts.stop()
        self.chapter_idx = max(self.chapter_idx - 1, 0)
        self.chunk_idx = 0
        self._load_current_chunks()
        self.preview_text = self._current_text()[:2000]
        self._save_progress()
        self.player_status = f"Cap {self.chapter_idx+1}/{len(self.chapters)}"


if __name__ == "__main__":
    AudioLibroApp().run()
