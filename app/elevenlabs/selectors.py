"""Semua URL dan selector ElevenLabs ada di sini.
Kalau UI-nya berubah, cukup ubah file ini.

Nilai yang diawali "TODO" belum diisi. Endpoint yang butuh selector itu
akan balas 501 sampai selector-nya diisi.
"""

import re

BASE_URL = "https://elevenlabs.io"
HOME_URL = f"{BASE_URL}/app/home"
TTS_URL = f"{BASE_URL}/app/speech-synthesis/text-to-speech"

# Kalau URL halaman mengandung ini, berarti cookie sudah tidak valid
LOGIN_URL_MARKERS = ("/sign-in", "/login", "/sign-up")

# --- Network (lebih tahan perubahan UI daripada DOM) ---
# Web app ElevenLabs memanggil API internalnya sendiri; kita "nguping" responsnya.
# Verifikasi di DevTools > Network kalau datanya tidak ketangkap.
# Request ke API internal (mis. https://api.us.elevenlabs.io/v1/...). Header authorization
# dari request ini dipakai ulang untuk memanggil endpoint di bawah secara langsung.
API_URL_RE = re.compile(r"^(https://api(?:\.[a-z0-9-]+)?\.elevenlabs\.io)/v\d+/")
SUBSCRIPTION_PATH = "/v1/user/subscription"
USER_PATH = "/v1/user"  # berisi field "subscription"; dipakai kalau SUBSCRIPTION_PATH gagal
# Voice library (yang dipanggil modal "More voices"), terverifikasi dari DevTools:
# ?required_languages=id&search=zan&sort=trending&page_size=30&page=0
SHARED_VOICES_PATH = "/v1/shared-voices"
# Generate suara (POST, response = MP3 utuh), terverifikasi dari DevTools:
# {"inputs":[{"text":..,"voice_id":..}],"model_id":"eleven_v4_exp",
#  "settings":{"stability":0.5,"similarity":0.75,"text_focus":false},"collect_preference":true}
DIALOGUE_STREAM_PATH = "/v1/text-to-dialogue/stream"

# --- DOM credit ---
# Avatar di kanan atas dikelilingi ring credit (svg viewBox 0 0 100 100); klik untuk buka menu.
AVATAR_TRIGGER = 'button:has(svg[viewBox="0 0 100 100"])'
# Di menu ada kartu "Balance" berisi baris "Total" & "Remaining" (nilai di .tabular-nums)
BALANCE_LABEL = "Balance"
BALANCE_VALUE = ".tabular-nums"

# --- DOM text-to-speech ---
# Editor = Tiptap/ProseMirror. Tiap speaker = satu block .node-dialogueNode
# (tombol "Add speaker" menambah block baru). Kita pakai block pertama.
TEXT_BLOCK = ".node-dialogueNode"
TEXT_INPUT = '[data-testid="tts-editor"]'                  # dicari di dalam TEXT_BLOCK
CLEAR_TEXT_BUTTON = 'button[aria-label="Clear text"]'      # dicari di dalam TEXT_BLOCK
VOICE_PICKER_BUTTON = 'button[aria-haspopup="dialog"]'     # dicari di dalam TEXT_BLOCK

# Pilih voice, langkah 1: dropdown kecil (cmdk) berisi "In use" + "Recents" + "More voices"
DROPDOWN_OPTION = '[cmdk-item][role="option"]:not([data-value="more-voices"])'
DROPDOWN_MORE = '[cmdk-item][data-value="more-voices"]'

# Pilih voice, langkah 2: modal besar (voice library)
MODAL_SEARCH = 'input[placeholder="Start typing to search..."]'
MODAL_ROW = "li.eleven-list-item"
MODAL_USE_BUTTON = 'button[aria-label^="Select {name}"]'   # tombol "Use"; {name} = awalan nama voice
MODAL_CLEAR_LANGUAGE = 'button[aria-label="Clear languages"]'

# Tombol generate; atribut data-loading="true" selama proses generate
GENERATE_BUTTON = '[data-testid="tts-generate"]'

# Download hasil: utamakan tombol di audio player bawah (pasti hasil terbaru),
# fallback ke tombol Download pertama di halaman.
AUDIO_PLAYER = '[data-testid="audio-player"]'
DOWNLOAD_BUTTON = 'button[aria-label="Download"]'
