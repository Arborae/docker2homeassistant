# Changelog

Tutte le modifiche rilevanti a questo progetto sono documentate in questo file.

Il formato si ispira a [Keep a Changelog](https://keepachangelog.com/it/1.0.0/)
e il progetto adotta un versionamento di tipo [SemVer](https://semver.org/lang/it/).

## [0.2.0] - 2026-06-20

### Added
- **PWA installabile**: aggiunti `manifest.json` e service worker (`sw.js`); l'app è ora
  installabile dal browser come applicazione standalone su Android, iOS e desktop.
- I requisiti PWA (manifest, registrazione del service worker, icone 192/512) sono ora
  presenti su **tutte le pagine d'ingresso** (login, splash, dashboard), così
  l'installazione è offerta già dalla schermata di accesso.
- Schermata di **splash** con health-check del backend durante l'avvio.
- Protezione **CSRF** per le richieste form, con esenzione delle API JSON tutelate dalla
  Same-Origin Policy.
- **Rate limiting** sugli endpoint `/api/*` di scrittura.
- **Timeout di sessione** configurabile (logout automatico per inattività).

### Changed
- **Refactor del service layer**: la logica Docker monolitica (`services/docker.py`) è stata
  riorganizzata in un **package modulare** (`services/docker/`) suddiviso per dominio:
  container, immagini/aggiornamenti, reti, volumi, sistema, eventi.
- Il rate limiting del login e delle API ora usa l'**IP reale del client** dietro reverse
  proxy (`CF-Connecting-IP` / primo hop di `X-Forwarded-For`) invece dell'indirizzo del proxy.
- I log di debug del flusso di aggiornamento container usano ora il **logger** (rispettando
  la redazione dei dati sensibili) invece di `print` su stderr.

### Fixed
- Le azioni sui container (`start`/`stop`/`restart`/`delete`/…) non riportano più un falso
  "successo" quando l'operazione Docker fallisce: gli errori vengono propagati e restituiti
  dall'API.
- `remove_image` non ignora più silenziosamente gli errori; la rimozione di un'immagine
  mostra ora un esito chiaro (successo/errore) nell'interfaccia.
- Corretto un errore di template nello splash (`{ { … } }` invece di `{{ … }}`) che generava
  JavaScript non valido.
- Validazione dell'input `minutes` nell'endpoint di frequenza aggiornamenti (niente più
  HTTP 500 su valori non numerici).

### Security
- **Modalità sicura coerente**: le azioni distruttive sui container (`delete`/`kill`)
  richiedono ora una conferma esplicita quando la modalità sicura è attiva (HTTP 403 senza
  conferma), in linea con quanto già previsto per le reti. L'interfaccia mostra un dialog
  di conferma prima dell'eliminazione (singola e in blocco).
- Mitigato l'aggiramento del blocco anti-bruteforce del login basato su header
  `X-Forwarded-For` falsificabile.
- Chiuso un edge case di open-redirect (`/\host`) nella validazione del parametro `next`.
- Redazione dei valori sensibili nei log (secret key, password MQTT, hash/segreti).

## [0.1.x]

- Versioni precedenti: dashboard Docker, gestione container/immagini/volumi/reti,
  aggiornamenti via label OCI, integrazione MQTT/Home Assistant, autenticazione con
  onboarding e 2FA TOTP, temi dark/light e multi-lingua IT/EN.
