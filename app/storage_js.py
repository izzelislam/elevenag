"""JavaScript untuk ekspor/impor data login (Firebase) ElevenLabs.

Login ElevenLabs = token Firebase `firebase:authUser:<apiKey>:[DEFAULT]`, yang bisa
tersimpan di IndexedDB `firebaseLocalStorageDb`, localStorage, atau sessionStorage
(tergantung setting persistence aplikasinya). Cookie saja tidak cukup.
"""

import json

AUTH_KEY_MARKER = "firebase:authUser"


def has_firebase_auth(storage: dict | None) -> bool:
    """True kalau dump storage berisi token login Firebase."""
    return bool(storage) and AUTH_KEY_MARKER in json.dumps(storage)


# Fungsi async (dipakai lewat page.evaluate) yang mengembalikan dump storage
DUMP_FN = r"""
async () => {
  const req = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const out = {
    localStorage: { ...localStorage },
    sessionStorage: { ...sessionStorage },
    indexedDB: [],
  };
  const dbs = indexedDB.databases ? await indexedDB.databases() : [{ name: "firebaseLocalStorageDb" }];
  for (const { name } of dbs) {
    if (!name || !/firebase/i.test(name)) continue;
    const db = await req(indexedDB.open(name));
    const stores = [];
    for (const s of db.objectStoreNames) {
      const os = db.transaction(s).objectStore(s);
      const [keys, values] = await Promise.all([req(os.getAllKeys()), req(os.getAll())]);
      stores.push({
        name: s,
        keyPath: os.keyPath,
        autoIncrement: os.autoIncrement,
        records: values.map((value, i) => (os.keyPath ? { value } : { key: keys[i], value })),
      });
    }
    out.indexedDB.push({ name, version: db.version, stores });
    db.close();
  }
  return out;
}
"""

# Fungsi async (page.evaluate(SEED_FN, storage)) untuk menulis localStorage + IndexedDB.
# sessionStorage per-tab, jadi diisi lewat init script (SESSION_SEED_FN).
SEED_FN = r"""
async (st) => {
  for (const [k, v] of Object.entries(st.localStorage || {})) localStorage.setItem(k, v);
  for (const db of st.indexedDB || []) {
    await new Promise((res, rej) => {
      const r = indexedDB.open(db.name, db.version || 1);
      r.onupgradeneeded = () => {
        const d = r.result;
        for (const s of db.stores) {
          if (d.objectStoreNames.contains(s.name)) continue;
          const opts = { autoIncrement: !!s.autoIncrement };
          if (s.keyPath !== null && s.keyPath !== undefined) opts.keyPath = s.keyPath;
          d.createObjectStore(s.name, opts);
        }
      };
      r.onerror = () => rej(r.error);
      r.onsuccess = () => {
        const d = r.result;
        const names = db.stores.map((s) => s.name).filter((n) => d.objectStoreNames.contains(n));
        if (!names.length) { d.close(); return res(); }
        const tx = d.transaction(names, "readwrite");
        for (const s of db.stores) {
          if (!names.includes(s.name)) continue;
          const os = tx.objectStore(s.name);
          for (const rec of s.records) (os.keyPath ? os.put(rec.value) : os.put(rec.value, rec.key));
        }
        tx.oncomplete = () => { d.close(); res(); };
        tx.onerror = () => rej(tx.error);
      };
    });
  }
}
"""

# Init script: isi sessionStorage di setiap tab elevenlabs.io sebelum script aplikasi jalan.
SESSION_SEED_FN = r"""
(data, origin) => {
  if (location.origin !== origin) return;
  try {
    for (const [k, v] of Object.entries(data)) {
      if (sessionStorage.getItem(k) === null) sessionStorage.setItem(k, v);
    }
  } catch (e) {}
}
"""


def session_seed_script(session_storage: dict, origin: str) -> str:
    return f"({SESSION_SEED_FN.strip()})({json.dumps(session_storage)}, {json.dumps(origin)});"


def build_payload_snippet(name: str | None, set_default: bool, endpoint_hint: str) -> str:
    """Snippet Console Chrome yang menghasilkan body request LENGKAP untuk
    POST /sessions (kalau `name` diisi) atau PUT /sessions/{id}/login (kalau tidak):
    storage (token login) + cookies (yang bisa dibaca JS) + user_agent.

    Hasil ditampilkan di panel kecil di halaman dengan tombol "Salin" (klik di halaman
    memberi izin clipboard), dan juga disimpan di window.__elevenag."""
    extra = {}
    if name:
        extra = {"name": name, "set_default": set_default}
    return (
        "(async () => {\n"
        "  // console cadangan dari iframe: halaman produksi sering mematikan console.log\n"
        "  const log = (() => {\n"
        "    try {\n"
        "      const f = document.createElement('iframe'); f.style.display = 'none';\n"
        "      document.documentElement.appendChild(f); return f.contentWindow.console;\n"
        "    } catch (e) { return console; }\n"
        "  })();\n"
        "  if (!location.hostname.endsWith('elevenlabs.io')) {\n"
        "    const msg = '❌ GAGAL: jalankan snippet ini di tab elevenlabs.io (sekarang: ' + location.hostname + ')';\n"
        "    log.error(msg); return msg;\n"
        "  }\n"
        f"  const dump = await ({DUMP_FN.strip()})();\n"
        f"  const M = {json.dumps(AUTH_KEY_MARKER)};\n"
        "  const where = [];\n"
        "  if (Object.keys(dump.localStorage).some((k) => k.includes(M))) where.push('localStorage');\n"
        "  if (Object.keys(dump.sessionStorage).some((k) => k.includes(M))) where.push('sessionStorage');\n"
        "  if (JSON.stringify(dump.indexedDB).includes(M)) where.push('IndexedDB');\n"
        "  if (!where.length) {\n"
        "    const msg = '❌ GAGAL: token login (firebase:authUser) tidak ditemukan. Pastikan tab ini sudah login, reload halaman, lalu jalankan lagi.';\n"
        "    log.error(msg); return msg;\n"
        "  }\n"
        f"  const payload = {{ ...{json.dumps(extra)}, storage: dump, user_agent: navigator.userAgent }};\n"
        "  if (document.cookie) payload.cookies = document.cookie;\n"
        "  const text = JSON.stringify(payload);\n"
        "  window.__elevenag = text;\n"
        "\n"
        "  // panel di halaman dengan tombol Salin\n"
        "  document.getElementById('__elevenag_panel')?.remove();\n"
        "  const box = document.createElement('div');\n"
        "  box.id = '__elevenag_panel';\n"
        "  Object.assign(box.style, { position: 'fixed', right: '16px', bottom: '16px', zIndex: 2147483647,\n"
        "    width: '380px', padding: '14px', borderRadius: '12px', background: '#111', color: '#fff',\n"
        "    font: '13px/1.4 system-ui, sans-serif', boxShadow: '0 8px 30px rgba(0,0,0,.35)' });\n"
        "  const title = document.createElement('div');\n"
        "  title.textContent = '✅ Payload session siap (' + Math.round(text.length / 1024) + ' KB)';\n"
        "  title.style.fontWeight = '600';\n"
        "  const info = document.createElement('div');\n"
        f"  info.textContent = 'Token login di: ' + where.join(', ') + '. Paste ke body ' + {json.dumps(endpoint_hint)} + '.';\n"
        "  Object.assign(info.style, { opacity: '.75', margin: '4px 0 10px' });\n"
        "  const btn = document.createElement('button');\n"
        "  const close = document.createElement('button');\n"
        "  for (const b of [btn, close]) Object.assign(b.style, { padding: '7px 12px', borderRadius: '8px',\n"
        "    border: '0', cursor: 'pointer', font: 'inherit', fontWeight: '600', marginRight: '6px' });\n"
        "  btn.textContent = 'Salin payload';\n"
        "  Object.assign(btn.style, { background: '#fff', color: '#111' });\n"
        "  close.textContent = 'Tutup';\n"
        "  Object.assign(close.style, { background: '#333', color: '#fff' });\n"
        "  btn.onclick = async () => {\n"
        "    try { await navigator.clipboard.writeText(text); }\n"
        "    catch (e) {\n"
        "      const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta);\n"
        "      ta.select(); document.execCommand('copy'); ta.remove();\n"
        "    }\n"
        "    btn.textContent = 'Tersalin ✓';\n"
        "  };\n"
        "  close.onclick = () => box.remove();\n"
        "  box.append(title, info, btn, close);\n"
        "  document.body.appendChild(box);\n"
        "  const msg = '✅ BERHASIL: token login ditemukan di ' + where.join(', ')\n"
        "    + ' (' + Math.round(text.length / 1024) + ' KB). Klik tombol \"Salin payload\" di pojok kanan bawah halaman, '\n"
        f"    + 'lalu paste ke body ' + {json.dumps(endpoint_hint)} + '. (Alternatif: ketik copy(__elevenag))';\n"
        "  log.log('%c' + msg, 'color:#16a34a;font-weight:bold');\n"
        "  return msg;\n"
        "})();\n"
    )


# Snippet lama (hanya storage). Hasilnya disimpan di window.__elevenag; user lalu
# menjalankan `copy(__elevenag)` (copy() DevTools tidak tersedia di dalam fungsi async).
CONSOLE_SNIPPET = (
    "(async () => {\n"
    f"  const dump = await ({DUMP_FN.strip()})();\n"
    f"  const M = {json.dumps(AUTH_KEY_MARKER)};\n"
    "  const where = [];\n"
    "  if (Object.keys(dump.localStorage).some((k) => k.includes(M))) where.push('localStorage');\n"
    "  if (Object.keys(dump.sessionStorage).some((k) => k.includes(M))) where.push('sessionStorage');\n"
    "  if (JSON.stringify(dump.indexedDB).includes(M)) where.push('IndexedDB');\n"
    "  if (!where.length) {\n"
    "    console.warn('Token login (firebase:authUser) TIDAK ditemukan. Pastikan tab ini sudah login, reload halaman, lalu jalankan lagi.');\n"
    "    console.log('IndexedDB:', (await indexedDB.databases()).map((d) => d.name));\n"
    "    console.log('localStorage keys:', Object.keys(dump.localStorage));\n"
    "    console.log('sessionStorage keys:', Object.keys(dump.sessionStorage));\n"
    "    return;\n"
    "  }\n"
    "  window.__elevenag = JSON.stringify(dump);\n"
    "  console.log('%cToken login ditemukan di: ' + where.join(', ') + '.', 'color:#16a34a;font-weight:bold');\n"
    "  console.log('%cSekarang ketik:  copy(__elevenag)  lalu Enter, untuk menyalin ke clipboard. Kirim sebagai field \"storage\".', 'font-weight:bold');\n"
    "})();\n"
)
