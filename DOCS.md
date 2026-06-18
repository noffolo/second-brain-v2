# Documentazione Tecnica del Secondo Cervello

Questa documentazione approfondisce l'architettura, i moduli di sincronizzazione avanzata, i demoni in background e la strategia del repository Git del Secondo Cervello.

---

## Indice
1. [Integrazione Google Calendar (iCal/ICS)](#1-integrazione-google-calendar-icalics)
2. [Sincronizzazione Notion](#2-sincronizzazione-notion)
3. [Briefing Daemon (Pre-Evento via Mail)](#3-briefing-daemon-pre-evento-via-mail)
4. [Nightly Dream Mode (Modalità Sogno)](#4-nightly-dream-mode-modalita-sogno)
5. [Strategia Git: Due Versioni del Repository](#5-strategia-git-due-versioni-del-repository)
6. [Gestione dei Servizi launchd su macOS](#6-gestione-dei-servizi-launchd-su-macos)
7. [Console Amministrazione (/admin) e Gestione Ontologica](#7-console-amministrazione-admin-e-gestione-ontologica)

---

## 1. Integrazione Google Calendar (iCal/ICS)

Il Secondo Cervello supporta la sincronizzazione degli eventi da calendari esterni in formato iCal (`.ics`).

### Configurazione
Nel file `settings.md` è presente la sezione:
```yaml
sources:
  google_calendar:
    enabled: true
    urls:
      - "https://calendar.google.com/calendar/ical/.../basic.ics"
```

> [!WARNING]
> **Nota sui Calendari Privati**:
> L'URL pubblico di Google Calendar (es. quello terminante con `@gmail.com/public/basic.ics`) restituisce un errore HTTP 404 se il calendario è impostato come privato.
> Per sincronizzare correttamente un calendario privato senza esporlo pubblicamente, devi recuperare l'**Indirizzo segreto in formato iCal** dalle impostazioni di Google Calendar:
> 1. Apri Google Calendar sul web.
> 2. Vai in Impostazioni per il calendario desiderato.
> 3. Scorri fino alla sezione "Integra calendario".
> 4. Copia l'URL del campo **Indirizzo segreto in formato iCal**.
> 5. Incolla questo URL nella lista `urls` in `settings.md`.

### Funzionamento del Sync
Durante l'esecuzione di `make ingest` o del daemon di sincronizzazione, lo script [calendar_tools.py](engine/tools/calendar_tools.py):
1. Scarica i file `.ics` dagli URL configurati.
2. Esegue il parsing degli eventi (DTSTART, DTEND, SUMMARY, DESCRIPTION, LOCATION, ORGANIZER, ATTENDEE).
3. Salva ogni evento come file markdown all'interno della cartella `raw/calendar/`.
4. L'Ingest Agent elabora successivamente questi file markdown trasformandoli in entità di tipo `event` o `appointment` nella cartella `wiki/entities/`.

---

## 2. Sincronizzazione Notion

Il modulo Notion consente una sincronizzazione bidirezionale avanzata.

### Prerequisiti (.env)
Aggiungi il token Notion in `.env`:
```env
NOTION_TOKEN=secret_yourNotionTokenHere
```

### Configurazione (settings.md)
Imposta gli ID dei database in `settings.md`:
```yaml
sources:
  notion:
    enabled: true
    sync_all: false
    calendar_database_id: "ID_DATABASE_EVENTI"
    tasks_database_id: "ID_DATABASE_TASK"
```

### Moduli di Sincronizzazione
- **Notion Calendar Sync** ([notion_calendar.py](engine/tools/notion_calendar.py)): Scarica gli eventi del calendario di Notion e li inserisce in `raw/notion/` come appuntamenti.
- **Notion Tasks Sync** ([notion_tasks.py](engine/tools/notion_tasks.py)): Esegue una sincronizzazione bidirezionale dei task.
  - I task di Notion vengono importati in `raw/notion/` e integrati nel vault.
  - Se un task viene contrassegnato come completato nel vault, lo stato viene aggiornato anche sul database Notion corrispondente.

---

## 3. Briefing Daemon (Pre-Evento via Mail)

Il `briefing_daemon` è un servizio proattivo che prepara l'utente agli appuntamenti della giornata.

### Come funziona
Ogni 5 minuti (configurabile in `settings.md` tramite `timing.briefing`), il demone scansiona gli eventi futuri estratti in `wiki/entities/` (tipo `event` o `appointment`).
1. Rileva se c'è un evento che inizierà esattamente **tra 15 minuti**.
2. Estrae il contesto dell'evento (titolo, descrizione, partecipanti).
3. Interroga il Secondo Cervello per trovare note, concetti correlati, verbali di riunioni precedenti e profili dei contatti del CRM coinvolti.
4. Un LLM (Gemini) sintetizza queste informazioni in un'email strutturata contenente:
   - **Obiettivo e Dettagli dell'evento**.
   - **Contesto rilevante** (estratti di note concetto o sintesi correlate).
   - **Persone chiave** (note del CRM sui partecipanti).
   - **Storico** (ultimi punti discussi nei verbali di riunione in `Meetings/`).
5. Invia l'email all'indirizzo dell'utente configurato in `.env`.

### Configurazione Email (.env)
Il demone utilizza SMTP per l'invio. Configura i parametri in `.env`:
```env
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tua_email@gmail.com
SMTP_PASSWORD=tua_app_password
EMAIL_RECEIVER=tua_email_ricevente@gmail.com
```

---

## 4. Nightly Dream Mode (Modalità Sogno)

Ogni notte alle 03:00, il Secondo Cervello entra in "Dream Mode" ([dream_daemon.py](engine/dream_daemon.py)).

### Obiettivi della Rielaborazione Notturna
L'agente effettua un'analisi profonda sull'intero grafo semantico delle note:
1. **Connessioni Invisibili**: Cerca note isolate o concetti semanticamente vicini ma non collegati esplicitamente, proponendo nuovi link.
2. **Anomalie e Contraddizioni**: Rileva se ci sono note con informazioni contrastanti (es. date di scadenze diverse o dettagli discordanti su un progetto).
3. **Generazione di Sintesi**: Scrive una nota di riepilogo in `wiki/synthesis/dream_YYYY-MM-DD.md` riassumendo le scoperte della notte e i suggerimenti ontologici.
4. **Aggiornamento del Profilo**: Se emergono nuovi focus di lavoro ricorrenti, suggerisce aggiornamenti per `user_profile.md`.

---

## 5. Strategia Git: Due Versioni del Repository

Per consentire sia la condivisione pubblica del motore di intelligenza artificiale sia il backup sicuro dei propri dati, abbiamo strutturato il repository in due modalità:

### A. Versione Clean (Codebase Pubblica)
Questa modalità contiene solo il codice sorgente, l'engine e gli script di configurazione, escludendo qualsiasi dato personale.
- **`.gitignore`**: È configurato per ignorare in modo preventivo le cartelle `wiki/`, `CRM/`, `journal/`, `Meetings/`, `raw/` e `scratch/`.
- **Untracking**: Tutti i dati precedentemente tracciati sono stati rimossi dall'indice del repository (`git rm --cached`).

#### Comando di Esportazione Codebase (`export-clean`)
Per creare una copia del solo codice pulito (ad esempio per caricarlo su una repo pubblica GitHub):
```bash
python engine/main.py export-clean /percorso/della/nuova/cartella
```
Questo comando legge i file attualmente tracciati da Git (tramite `git ls-files`) e li copia nella cartella di destinazione ricreando la struttura, escludendo automaticamente tutti i dati personali e le chiavi segrete `.env`.

### B. Versione Snapshot (Con Dati)
La cartella locale del tuo secondo cervello (`second_brain`) contiene tutto il materiale. Per effettuare il backup completo (codice + dati personali) in modo sicuro:
1. Mantieni questa cartella locale come repository Git principale.
2. Collegala a un **repository privato** su GitHub o GitLab.
3. Se desideri tracciare anche i file del vault in questo repository privato, puoi creare un branch di backup (es. `backup-vault`) in cui rimuovi le regole di esclusione dal file `.gitignore` e fai il commit di tutto.
*Nota bene: L'uso di `export-clean` in una directory separata è il metodo consigliato per evitare che comandi Git accidentali cancellino file non tracciati locali durante cambi di branch.*

---

## 6. Gestione dei Servizi launchd su macOS

Il Secondo Cervello automatizza le sue operazioni tramite 4 agenti in background nativi macOS (`launchd`).

### Elenco dei Servizi
1. **`com.secondbrain.sync`**: Esegue il sync delle fonti ed ingestion dei file. Attivo ogni giorno alle 10:00 AM.
2. **`com.secondbrain.reflect`**: Esegue l'agente di riflessione settimanale. Attivo ogni domenica alle 21:00.
3. **`com.secondbrain.briefing`**: Esegue il controllo pre-evento per inviare briefing via mail. Attivo ogni 5 minuti.
4. **`com.secondbrain.dream`**: Esegue la rielaborazione notturna ("Dream Mode"). Attivo ogni notte alle 03:00 AM.

### Comandi Utili (Makefile)
- **Installare e caricare tutti i demoni**:
  ```bash
  make install-service
  ```
  Questo legge le pianificazioni da `settings.md`, genera i file `.plist` in `~/Library/LaunchAgents/` e li carica in macOS.
- **Rimuovere e disattivare tutti i demoni**:
  ```bash
  make uninstall-service
  ```
- **Controllare lo stato dei servizi**:
  ```bash
  launchctl list | grep secondbrain
  ```
- **Vedere i log dei servizi**:
  I log dello standard output e di errore dei demoni vengono salvati in `~/Library/Logs/SecondBrain/`.

---

## 7. Console Amministrazione (/admin) e Gestione Ontologica

Il Secondo Cervello dispone di una Console di Amministrazione separata all'indirizzo `/admin` (integrata in `dashboard.py`), progettata con un'estetica dark/glassmorphic premium. Questa console consente la gestione completa del sistema senza bloccare l'esecuzione automatica dei processi.

### Funzionalità della Console
- **Logs in tempo reale**: visualizzazione in streaming (tramite SSE) dei log delle operazioni in background del Secondo Cervello.
- **Editor dei Prompt e Profilo**: consente di visualizzare e modificare singolarmente i prompt operativi degli agenti (estratti dinamicamente dal file `agents.md`) e la Working Memory in `user_profile.md`.
- **Gestione Automazioni**: permette di abilitare, disabilitare e forzare l'esecuzione immediata di demoni in background (Briefing, Dream Mode, Sync) e configurarne i parametri operativi.
- **Ingestione Manuale**: monitoraggio dei file in coda di elaborazione (`raw/`) e pulsante di trigger per l'avvio immediato dell'Ingest Agent.
- **Configurazione di Sistema**: editor visuale dei parametri di configurazione definiti in `settings.md`.

### Gestione Ontologica Non-Bloccante e Rollback
L'Ontology Agent opera asincronamente per riorganizzare, fondere o collegare concetti ed entità del vault. Per eliminare ogni blocco e consentire comunque il controllo all'utente:
1. **Esecuzione Automatica**: l'agente esegue le sue decisioni senza attendere il consenso preventivo dell'utente.
2. **Backup Preventivo delle Transazioni**: prima di qualsiasi modifica (fusione di note in `merge_nodes`, definizione di relazioni gerarchiche in `set_parent`, o aggiornamento dei collegamenti in `update_links_in_vault`), i file originali nello stato pulito prima della transazione vengono copiati all'interno di `engine/ontology_backups/<proposal_id>/`.
3. **Rollback ed Annullamento**: dalla Console Admin, l'operatore può visualizzare la cronologia delle transazioni ontologiche ed effettuare un **Rollback** in tempo reale. Il sistema ripristinerà i file originali direttamente dal backup per quella specifica transazione e ripulirà la directory temporanea.
4. **Conferma**: se l'utente accetta le modifiche, la cartella di backup viene rimossa per liberare spazio.
