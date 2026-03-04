import re
import json
from pathlib import Path

from kivy.app import App
from kivy.lang import Builder
from kivy.clock import mainthread
from kivy.uix.boxlayout import BoxLayout
from kivy.metrics import dp

from plyer import filechooser

from ebooklib import epub
from bs4 import BeautifulSoup

# ---------------------------
# UI
# ---------------------------
KV = r"""
<RootUI>:
    orientation: "vertical"
    padding: dp(12)
    spacing: dp(10)

    Label:
        id: lbl_title
        text: root.title_text
        bold: True
        size_hint_y: None
        height: self.texture_size[1] + dp(6)

    Label:
        id: lbl_status
        text: root.status_text
        size_hint_y: None
        height: self.texture_size[1] + dp(6)

    BoxLayout:
        size_hint_y: None
        height: dp(44)
        spacing: dp(8)

        Button:
            text: "Cargar EPUB"
            on_release: root.pick_epub()

        Button:
            text: "▶ Play"
            on_release: root.play()

        Button:
            text: "⏸ Pausa"
            on_release: root.pause()

    BoxLayout:
        size_hint_y: None
        height: dp(44)
        spacing: dp(8)

        Button:
            text: "⏮ Anterior"
            on_release: root.prev_chapter()

        Button:
            text: "⏭ Siguiente"
            on_release: root.next_chapter()

        Button:
            text: "↩ Reanudar"
            on_release: root.resume()

    BoxLayout:
        size_hint_y: None
        height: dp(44)
        spacing: dp(8)

        Label:
            text: "Velocidad"
            size_hint_x: None
            width: dp(90)

        Slider:
            id: speed
            min: 0.5
            max: 2.0
            value: 1.0
            on_value: root.set_rate(self.value)

        Label:
            text: "{:.2f}x".format(speed.value)
            size_hint_x: None
            width: dp(60)

    Label:
        text: "Vista previa (capítulo actual):"
        size_hint_y: None
        height: self.texture_size[1] + dp(4)

    TextInput:
        id: preview
        text: root.preview_text
        readonly: True
        multiline: True
"""


# ---------------------------
# EPUB parsing
# ---------------------------
def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Quitar ruido
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    # Intento de quitar notas al pie comunes (EPUB depende)
    for sel in ["sup", ".footnote", ".endnote", ".notes", "[epub\\:type=noteref]"]:
        try:
            for t in soup.select(sel):
                t.decompose()
        except Exception:
            pass

    text = soup.get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def epub_to_chapters(epub_path: str):
    book = epub.read_epub(epub_path)
    chapters = []

    # ITEM_DOCUMENT = 9 (ebooklib)
    for item in book.get_items():
        if item.get_type() == 9:
            raw = item.get_content().decode("utf-8", errors="ignore")
            txt = html_to_text(raw)
            if len(txt) > 400:  # filtra portada/índices cortos
                chapters.append(txt)

    title = "EPUB"
    try:
        metas = book.get_metadata("DC", "title")
        if metas and metas[0] and metas[0][0]:
            title = metas[0][0]
    except Exception:
        pass

    return title, chapters


def chunk_text(text: str, max_len: int = 900):
    # troceo por frases para poder reanudar “casi exacto”
    sentences = (
        text.replace("\n", " ")
        .split()
    )
    # reconstruimos en chunks por longitud aproximada
    out, buf, cur = [], [], 0
    for w in sentences:
        if cur + len(w) + 1 > max_len and buf:
            out.append(" ".join(buf).strip())
            buf, cur = [], 0
        buf.append(w)
        cur += len(w) + 1
    if buf:
        out.append(" ".join(buf).strip())
    return out


# ---------------------------
# Android TTS via PyJNIus
# ---------------------------
class AndroidTTS:
    def __init__(self, on_done=None):
        self.tts = None
        self.ready = False
        self.rate = 1.0
        self.on_done = on_done  # callback when an utterance completes

        try:
            from jnius import autoclass, PythonJavaClass, java_method

            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            Locale = autoclass("java.util.Locale")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")

            UtteranceProgressListener = autoclass("android.speech.tts.UtteranceProgressListener")

            class Listener(PythonJavaClass):
                __javainterfaces__ = ["android/speech/tts/TextToSpeech$OnInitListener"]
                __javacontext__ = "app"

                def __init__(self, outer):
                    super().__init__()
                    self.outer = outer

                @java_method("(I)V")
                def onInit(self, status):
                    if status == 0:  # SUCCESS
                        self.outer.ready = True
                        try:
                            self.outer.tts.setLanguage(Locale("es", "ES"))
                            self.outer.tts.setSpeechRate(float(self.outer.rate))
                        except Exception:
                            pass

            class ProgListener(PythonJavaClass):
                __javainterfaces__ = ["android/speech/tts/UtteranceProgressListener"]
                __javacontext__ = "app"

                def __init__(self, outer):
                    super().__init__()
                    self.outer = outer

                @java_method("(Ljava/lang/String;)V")
                def onStart(self, utteranceId):
                    # no-op
                    return

                @java_method("(Ljava/lang/String;)V")
                def onDone(self, utteranceId):
                    if self.outer.on_done:
                        self.outer.on_done(str(utteranceId))

                @java_method("(Ljava/lang/String;)V")
                def onError(self, utteranceId):
                    if self.outer.on_done:
                        self.outer.on_done(str(utteranceId))

            activity = PythonActivity.mActivity
            self.tts = TextToSpeech(activity, Listener(self))
            self.tts.setOnUtteranceProgressListener(ProgListener(self))

        except Exception:
            self.tts = None
            self.ready = False

    def set_rate(self, r: float):
        self.rate = max(0.5, min(2.0, float(r)))
        if self.tts and self.ready:
            try:
                self.tts.setSpeechRate(self.rate)
            except Exception:
                pass

    def speak(self, text: str, utterance_id: str = "utt"):
        if not self.tts or not self.ready:
            return False
        try:
            # QUEUE_FLUSH = 0 (corta lo anterior)
            self.tts.speak(text, 0, None, utterance_id)
            return True
        except Exception:
            return False

    def stop(self):
        if self.tts and self.ready:
            try:
                self.tts.stop()
            except Exception:
                pass


# ---------------------------
# App logic
# ---------------------------
class RootUI(BoxLayout):
    title_text = "Sin libro cargado"
    status_text = "Pulsa 'Cargar EPUB'"
    preview_text = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # estado libro
        self.book_title = ""
        self.book_path = ""
        self.chapters = []
        self.chapter_idx = 0

        # estado lectura
        self.chunks = []
        self.chunk_idx = 0
        self.playing = False

        self.tts = AndroidTTS(on_done=self._on_utterance_done)

        # progreso persistente
        self.progress_path = Path(App.get_running_app().user_data_dir) / "progress.json" \
            if App.get_running_app() else None

    def _progress_file(self):
        # user_data_dir solo existe con app corriendo
        base = Path(App.get_running_app().user_data_dir)
        return base / "progress.json"

    def save_progress(self):
        try:
            data = {
                "book_path": self.book_path,
                "chapter_idx": self.chapter_idx,
                "chunk_idx": self.chunk_idx,
                "rate": float(self.ids.speed.value),
            }
            self._progress_file().write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def load_progress(self):
        try:
            p = self._progress_file()
            if not p.exists():
                return None
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    @mainthread
    def _set_ui(self, title=None, status=None, preview=None):
        if title is not None:
            self.title_text = title
            self.ids.lbl_title.text = title
        if status is not None:
            self.status_text = status
            self.ids.lbl_status.text = status
        if preview is not None:
            self.preview_text = preview
            self.ids.preview.text = preview

    def set_rate(self, r):
        self.tts.set_rate(r)
        self.save_progress()

    def pick_epub(self):
        filechooser.open_file(
            filters=[("EPUB files", "*.epub"), ("All files", "*.*")],
            on_selection=self._on_file_selected,
        )

    def _on_file_selected(self, selection):
        if not selection:
            self._set_ui(status="Selección cancelada")
            return
        path = selection[0]
        self._set_ui(status="Leyendo EPUB...")

        try:
            title, chapters = epub_to_chapters(path)
            if not chapters:
                self._set_ui(title=f"Libro: {title}", status="No encontré capítulos útiles.", preview="")
                return

            self.book_title = title
            self.book_path = path
            self.chapters = chapters
            self.chapter_idx = 0
            self.chunk_idx = 0
            self._load_current_chunks()

            self._set_ui(
                title=f"Libro: {title}",
                status=f"Capítulos: {len(chapters)} · En {self.chapter_idx+1}",
                preview=self._current_text()[:2000],
            )

            # aplica progreso previo si es el mismo libro
            prev = self.load_progress()
            if prev and prev.get("book_path") == self.book_path:
                self.chapter_idx = int(prev.get("chapter_idx", 0))
                self.chunk_idx = int(prev.get("chunk_idx", 0))
                rate = float(prev.get("rate", 1.0))
                self.ids.speed.value = rate
                self.tts.set_rate(rate)
                self._load_current_chunks()
                self._set_ui(status=f"Progreso cargado · Cap {self.chapter_idx+1} · Chunk {self.chunk_idx+1}")

            self.save_progress()

        except Exception as e:
            self._set_ui(status=f"Error leyendo EPUB: {e}")

    def _current_text(self):
        if not self.chapters:
            return ""
        return self.chapters[self.chapter_idx]

    def _load_current_chunks(self):
        self.chunks = chunk_text(self._current_text())
        self.chunk_idx = max(0, min(self.chunk_idx, max(0, len(self.chunks) - 1)))

    def _speak_current_chunk(self):
        if not self.chunks:
            self._load_current_chunks()
        if not self.chunks:
            return

        ok = self.tts.speak(self.chunks[self.chunk_idx], utterance_id=f"chunk_{self.chunk_idx}")
        if not ok:
            self._set_ui(status="TTS no listo (instala voz en español en el móvil).")
            self.playing = False
            return

        self.playing = True
        self.save_progress()
        self._set_ui(status=f"▶ Cap {self.chapter_idx+1}/{len(self.chapters)} · Parte {self.chunk_idx+1}/{len(self.chunks)}")

    def play(self):
        if not self.chapters:
            self._set_ui(status="Primero carga un EPUB.")
            return
        self._speak_current_chunk()

    def pause(self):
        self.tts.stop()
        self.playing = False
        self.save_progress()
        self._set_ui(status="⏸ Pausado")

    def resume(self):
        if not self.chapters:
            self._set_ui(status="Primero carga un EPUB.")
            return
        self._speak_current_chunk()

    def next_chapter(self):
        if not self.chapters:
            return
        self.tts.stop()
        self.chapter_idx = min(self.chapter_idx + 1, len(self.chapters) - 1)
        self.chunk_idx = 0
        self._load_current_chunks()
        self.save_progress()
        self._set_ui(
            status=f"Capítulo {self.chapter_idx+1}/{len(self.chapters)}",
            preview=self._current_text()[:2000],
        )

    def prev_chapter(self):
        if not self.chapters:
            return
        self.tts.stop()
        self.chapter_idx = max(self.chapter_idx - 1, 0)
        self.chunk_idx = 0
        self._load_current_chunks()
        self.save_progress()
        self._set_ui(
            status=f"Capítulo {self.chapter_idx+1}/{len(self.chapters)}",
            preview=self._current_text()[:2000],
        )

    # Callback desde Android TTS cuando termina un chunk
    def _on_utterance_done(self, utterance_id: str):
        # avanzar automáticamente si está en modo reproduciendo
        if not self.playing:
            return
        # utterance_id = "chunk_X"
        if utterance_id.startswith("chunk_"):
            try:
                idx = int(utterance_id.split("_", 1)[1])
            except Exception:
                idx = self.chunk_idx
            # avanzamos a siguiente chunk
            self.chunk_idx = idx + 1

        if self.chunk_idx < len(self.chunks):
            self._speak_current_chunk()
        else:
            # fin capítulo -> parar (o auto-next si quieres)
            self.playing = False
            self.save_progress()
            self._set_ui(status="✅ Capítulo terminado (pulsa Siguiente o Play)")

class AudioLibroApp(App):
    def build(self):
        Builder.load_string(KV)
        return RootUI()

if __name__ == "__main__":
    AudioLibroApp().run()
