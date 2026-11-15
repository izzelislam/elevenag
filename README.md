# ElevenLabs Automation API

FastAPI + Playwright. Satu Chromium dipakai bersama, tiap request pakai context terpisah berisi cookie akun yang dipilih.

## Jalankan lokal (tanpa Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env        # isi API_KEYS
uvicorn app.main:app --reload --port 8000
```

Dokumentasi interaktif: http://localhost:8000/docs (klik **Authorize**, isi API key).

## Jalankan dengan Docker (DB tetap di host)

```bash
cp .env.example .env        # isi API_KEYS
docker compose up -d --build
```

- **SQLite**: file DB ada di `./data/app.db` di mesin host (di-mount ke container).
- **MySQL di host**: tambahkan di `.env`
  `DOCKER_DATABASE_URL=mysql+pymysql://user:pass@host.docker.internal:3306/elevenag`
  User MySQL harus boleh connect dari selain `localhost` (mis. `'user'@'%'`), lalu buat database-nya dulu. Tabel dibuat otomatis.
- Di Linux, folder `./data` harus bisa ditulis UID 1000: `sudo chown -R 1000 data`.

## Endpoint

Semua (kecuali `/health`) butuh header `X-API-Key`.

| Method | Path | Fungsi |
|---|---|---|
| GET | `/sessions/snippet?name=` | Snippet Console Chrome yang menghasilkan body session lengkap |
| POST | `/sessions` | Simpan akun baru (`name`, `storage`, `cookies?`, `user_agent?`, `set_default?`) |
| GET | `/sessions` | Daftar session (data login tidak pernah dikembalikan) |
| GET | `/sessions/{id}` | Detail session + credit terakhir |
| PUT | `/sessions/{id}/login` | Perbarui data login (kalau session expired) |
| POST | `/sessions/{id}/default` | Jadikan session default |
| DELETE | `/sessions/{id}` | Hapus session |
| GET | `/credits?session_id=` | Cek sisa/pemakaian credit secara live |
| GET | `/voices?session_id=` | Daftar voice di akun |
| POST | `/tts` | Buat voice over (`text`, `voice`, `session_id?`) → `202` + job |
| GET | `/jobs` | Riwayat job (`?status=done`) |
| GET | `/jobs/{id}` | Status job |
| GET | `/jobs/{id}/download` | Download audio |
| POST | `/jobs/{id}/webhook` | Kirim ulang webhook job |

Kalau `session_id` tidak dikirim, dipakai session default (session pertama otomatis jadi default).

### Ambil data login dari Chrome

Login ElevenLabs disimpan di **IndexedDB** (Firebase), bukan di cookie. Karena itu export cookie saja tidak cukup.

1. Login ke elevenlabs.io di Chrome.
2. Ambil snippet dari `GET /sessions/snippet`:
   - session **baru**: `GET /sessions/snippet?name=akun-utama` (opsional `&set_default=true`)
   - **perbarui** session yang ada / expired: `GET /sessions/snippet` (tanpa `name`)
3. Di tab elevenlabs.io, buka DevTools > **Console**, paste snippet-nya, lalu Enter.
   (Kalau Chrome menolak paste, ketik `allow pasting` dulu lalu Enter.)
4. Klik tombol **Salin payload** di pojok kanan bawah halaman.
5. Paste sebagai body request apa adanya. Isinya sudah lengkap: `storage` + `cookies` + `user_agent` (+ `name`).

```bash
# session baru (snippet dengan ?name=...)
curl -X POST localhost:8000/sessions -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '<isi clipboard>'

# session yang sudah ada / expired (snippet tanpa name)
curl -X PUT localhost:8000/sessions/1/login -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '<isi clipboard>'

curl -X POST localhost:8000/tts -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"text":"Halo dunia","voice":"Rachel"}'
# -> {"id":"<job_id>","status":"queued",...}

curl -H "X-API-Key: $KEY" localhost:8000/jobs/<job_id>
curl -H "X-API-Key: $KEY" -OJ localhost:8000/jobs/<job_id>/download
```

Cookie terbaru otomatis disimpan ulang setelah tiap aksi, jadi session lebih awet.

## Auto-switch saat credit habis

Kalau `POST /tts` tanpa `session_id`, job dicoba di session default. Bila ElevenLabs menolak karena
credit habis, session itu ditandai `exhausted`, **default otomatis pindah** ke session berikutnya
yang masih usable, dan job diulang di sana. Kalau semua session habis:

- `POST /tts` langsung membalas **402** `{"detail": {"code": "insufficient_credit", ...}}`
- job yang sedang jalan berakhir `failed` dengan `error_code: "insufficient_credit"` (juga ada di webhook)

Session `exhausted` aktif lagi otomatis setelah `credits_reset_at`, atau manual lewat
`POST /sessions/{id}/reactivate` (mis. setelah top-up). Kalau client memilih `session_id` sendiri,
tidak ada pindah session (langsung 402 bila habis). Session `expired` dilewati saat memilih berikutnya.
Deteksi credit habis berdasarkan HTTP 402 / teks kuota di respons ElevenLabs; `GET /credits` yang
menunjukkan sisa 0 juga menandai habis.

## Webhook

Saat job selesai atau gagal, API mengirim `POST` ke `webhook_url`, yang diisi per request di `POST /tts` atau lewat `WEBHOOK_URL` di `.env`.

```json
{
  "event": "job.completed",
  "sent_at": "2026-11-15T07:00:00Z",
  "job": {
    "id": "…", "status": "done", "session_id": 1,
    "voice": "Zan - Conversational", "voice_id": "zmqLb9Ysr8fUvDD7hXK8", "model_id": "eleven_v4_exp",
    "source": "network", "error": null, "text_length": 42,
    "created_at": "…", "started_at": "…", "finished_at": "…",
    "download_url": "http://localhost:8000/jobs/…/download"
  }
}
```
`event` bernilai `job.failed` kalau gagal; `error` berisi alasannya dan `download_url` bernilai `null`. Mengunduh dari `download_url` tetap butuh header `X-API-Key`.

Header yang dikirim: `X-Elevenag-Event`, `X-Elevenag-Delivery` (id unik), `X-Elevenag-Timestamp`, dan kalau `WEBHOOK_SECRET` diisi, `X-Elevenag-Signature: sha256=<hex>`. Signature-nya adalah HMAC-SHA256 dari `"<timestamp>.<raw body>"`. Contoh verifikasi di penerima (Python):

```python
expected = "sha256=" + hmac.new(SECRET, f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()
ok = hmac.compare_digest(expected, request.headers["X-Elevenag-Signature"])
```

Kalau penerima tidak membalas 2xx, pengiriman dicoba lagi sampai 4 kali (jeda 5 detik, 30 detik, 2 menit). Status pengiriman ada di field `webhook_status`, `webhook_attempts`, dan `webhook_error` pada job. Kirim ulang manual: `POST /jobs/{id}/webhook` (opsional `?webhook_url=` untuk ganti tujuan).

## Selector

Semua URL/selector ada di [app/elevenlabs/selectors.py](app/elevenlabs/selectors.py). Selector yang masih `TODO` membuat `/tts` gagal dengan pesan jelas. Kalau job gagal, screenshot halaman disimpan di `data/debug/<job_id>.png`. Set `HEADLESS=false` untuk melihat browsernya langsung saat debugging.

## Catatan

- Jalankan **1 worker** saja: antrean job dan browser ada di dalam proses. Job yang sedang jalan saat server restart ditandai `failed`.
- `MAX_CONCURRENCY` membatasi jumlah browser context paralel. Satu akun selalu diproses satu per satu.
