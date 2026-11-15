from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Satu atau lebih API key, dipisah koma. Wajib diisi.
    api_keys: str = ""

    # SQLite (default) atau MySQL:
    #   sqlite:///./data/app.db
    #   mysql+pymysql://user:pass@127.0.0.1:3306/elevenag
    database_url: str = "sqlite:///./data/app.db"

    data_dir: Path = Path("./data")

    headless: bool = True
    # Proxy untuk SEMUA trafik keluar ke ElevenLabs (browser + panggilan API langsung).
    # Contoh Cloudflare WARP (mode proxy): socks5://127.0.0.1:40000
    # Kosong = langsung dari IP server.
    proxy_url: str = ""
    # Maksimal context browser yang jalan paralel (tiap context ~150-300MB RAM)
    max_concurrency: int = 2
    nav_timeout_ms: int = 30_000
    # Aplikasi ElevenLabs berat: saat cache kosong bisa ~45 detik sampai siap
    app_load_timeout_ms: int = 90_000
    generate_timeout_ms: int = 180_000
    # Context browser per akun dipakai ulang; ditutup kalau menganggur selama ini
    context_idle_minutes: int = 15
    # Header auth web app dipakai ulang selama ini (token Firebase berlaku 60 menit)
    auth_cache_minutes: int = 40

    # Webhook saat job selesai/gagal. WEBHOOK_URL = default kalau request tidak mengirim webhook_url.
    webhook_url: str = ""
    # Kalau diisi, tiap webhook ditandatangani (header X-Elevenag-Signature, HMAC-SHA256)
    webhook_secret: str = ""
    # Alamat API ini dari sisi penerima webhook (untuk download_url di payload)
    public_base_url: str = "http://localhost:8000"

    # Default generate (sama dengan yang dikirim web app); bisa di-override per request
    default_model_id: str = "eleven_v4_exp"
    # Dipakai kalau request /tts tidak mengirim voice / voice_id
    default_voice_id: str = "zmqLb9Ysr8fUvDD7hXK8"
    default_voice_name: str = "Zan - Conversational"  # untuk jalur UI (pilih voice by nama)
    default_stability: float = 0.5
    default_similarity: float = 0.75

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def download_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def debug_dir(self) -> Path:
        return self.data_dir / "debug"


settings = Settings()
