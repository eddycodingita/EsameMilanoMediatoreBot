# Monitor sessioni esame mediatori — Milano

Controlla ogni 10 minuti la pagina delle sessioni d'esame della Camera di Commercio
di Milano Monza Brianza Lodi e manda un messaggio Telegram appena compare una data
nuova o si liberano posti.

Pagina monitorata: <https://eolmi.infocamere.it/fnmiWeb/immobiliari?client=milano>

---

## 1. Crea il bot Telegram (3 minuti)

1. Su Telegram apri una chat con **@BotFather**, manda `/newbot` e segui le istruzioni.
2. Alla fine ricevi un token del tipo `8123456789:AAH...`. Tienilo da parte.
3. Manda un messaggio qualsiasi ("ciao") al tuo nuovo bot: senza questo passaggio il
   bot non può scriverti per primo.
4. Apri nel browser `https://api.telegram.org/bot<IL_TUO_TOKEN>/getUpdates` e copia il
   valore di `"chat":{"id": ...}`. Quello è il tuo **chat id**.

## 2. Crea il repository

1. Su GitHub crea un repository **pubblico** chiamato ad esempio `monitor-esame`.
   Pubblico non è un dettaglio: sui repo pubblici i minuti di GitHub Actions sono
   illimitati, su quelli privati il piano gratuito dà 2.000 minuti al mese e con un
   controllo ogni 10 minuti li esaurisci in circa una settimana. Nel codice non c'è
   nulla di riservato: token e chat id restano nei Secrets, che non sono pubblici.
2. Carica i file mantenendo questa struttura:

```
monitor-esame/
├─ monitor.py
├─ requirements.txt
└─ .github/
   └─ workflows/
      └─ monitor-esame.yml
```

   Attenzione: il file `monitor-esame.yml` va messo dentro `.github/workflows/`,
   non nella cartella principale.

## 3. Inserisci le credenziali

Nel repository: **Settings → Secrets and variables → Actions → New repository secret**.
Creane due:

| Nome | Valore |
|---|---|
| `TELEGRAM_TOKEN` | il token di BotFather |
| `TELEGRAM_CHAT_ID` | il chat id del punto 1.4 |

## 4. Avvia

Vai su **Actions → Monitor esame mediatori → Run workflow** per il primo giro manuale.
Se tutto è a posto ricevi un messaggio "Monitor attivo" con lo stato attuale della
pagina e uno screenshot. Da lì in poi parte da solo ogni 10 minuti.

---

## Come funziona

- Apre la pagina con Chromium vero, quindi il contenuto generato via JavaScript viene
  eseguito (un semplice `curl` sulla pagina restituisce 404).
- Estrae solo le righe che contengono una data o parole come *posti, disponibili,
  sessione, esaurito, iscriviti*, così i cambiamenti irrilevanti non generano rumore.
- Confronta con lo snapshot precedente salvato in `state/snapshot.json` e ricommittato
  a ogni giro: hai anche lo storico delle variazioni nei commit.
- Se rileva un numero di posti maggiore di zero il messaggio arriva con l'intestazione
  **POSTI DISPONIBILI**; per ogni altra modifica arriva un avviso generico di cambio pagina.
- Se il portale non risponde o cambia struttura, dopo 3 tentativi falliti ricevi un
  avviso: così il silenzio non viene scambiato per "nessuna novità".

## Cose da sapere

- I cron di GitHub Actions non sono al secondo: sotto carico partono con 5-15 minuti di
  ritardo. Se vuoi stringere, metti `*/5` al posto di `*/10` nel workflow.
- Al primo messaggio di allerta apri subito il portale: le sessioni nuove escono circa
  14 giorni prima e solo a esaurimento della precedente, quindi i posti spariscono in fretta.
  Tieni pronti SPID e attestato del corso.
- Se dopo il primo run il messaggio arriva vuoto o senza date, mandami lo screenshot che
  il bot ti invia: adatto i filtri delle righe alla struttura reale della tabella.
- Per fermare tutto: **Actions → Monitor esame mediatori → ⋯ → Disable workflow**.
