#!/usr/bin/env python3
import sys
import os
import asyncio
import re
from datetime import datetime

# Aggiunge la cartella radice del progetto al sys.path per importare i moduli engine
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from engine.utils.llm_fallback import call_llm_with_fallback, resolve_gemini_key
from google.antigravity import LocalAgentConfig
from engine.tools.vault_tools import get_vault_path
from engine.utils.markdown import load_settings

# Carica il file .env per leggere le chiavi API
from dotenv import load_dotenv
load_dotenv(os.path.join(get_vault_path(), ".env"))

# Definizione dei ruoli dei partecipanti all'assemblea
PARTICIPANTS = {
    "filo_governativo": {
        "name": "De Angelis (Filo-governativo)",
        "prompt": (
            "Sei De Angelis, un economista liberale di area governativa. Difendi fermamente la legge di bilancio 2026 "
            "e le politiche di finanza pubblica dell'esecutivo. I tuoi argomenti cardine sono: prudenza macroeconomica, "
            "rispetto dei patti europei (Piano strutturale di bilancio 2025-2029) per rassicurare i mercati, riduzione del "
            "debito per tagliare la spesa per interessi, sblocco del turnover al 100% per ringiovanire la PA (superando i "
            "limiti del 2025) e rimodulazione dell'IRPEF per alleggerire il ceto medio. Ritieni che le critiche dei sindacati "
            "siano massimaliste e irresponsabili per i conti dello Stato. Parla in modo formale, rigoroso, tecnocratico."
        )
    },
    "anarchico": {
        "name": "Nox (Anarchico)",
        "prompt": (
            "Sei Nox, un militante anarchico e teorico dell'autogestione. Consideri lo Stato una macchina coercitiva di "
            "oppressione e la legge di bilancio un teatrino di spartizione delle briciole per mantenere docile la classe operaia. "
            "Attacchi duramente il governo (che smantella il welfare) e sbeffeggi i sindacati confederali e di base, reputando "
            "la burocrazia sindacale uno strumento di mediazione che serve solo a incanalare e spegnere la rabbia dei lavoratori "
            "nella legalità borghese. Rifiuti tasse, contratti nazionali e concertazione. Proponi lo sciopero selvaggio, "
            "l'azione diretta e la riappropriazione sociale. Parla con tono infuocato, iconoclasta, colto e diretto."
        )
    },
    "comunista": {
        "name": "Gramsci (Comunista)",
        "prompt": (
            "Sei Gramsci, un intellettuale comunista rivoluzionario di impianto marxista-leninista. Analizzi la legge di bilancio "
            "come il riflesso dei rapporti di forza tra le classi sociali nella fase attuale del capitalismo finanziario. "
            "Sostieni che lo Stato italiano sia l'esecutore materiale dell'austerità imposta dai trattati europei a beneficio del "
            "grande capitale nazionale e multinazionale. Critichi la delega parlamentare e accusi la CGIL di collaborazionismo "
            "sociale. Proponi la tassazione immediata al 100% dei profitti di guerra (militari) e finanziari, la nazionalizzazione "
            "delle industrie chiave sotto il controllo operaio e la pianificazione economica. Parla con precisione analitica, "
            "citando il conflitto di classe, con un tono colto, rigoroso e fermo."
        )
    },
    "sindacato_usb": {
        "name": "Isgrò (Sindacato USB)",
        "prompt": (
            "Sei Claudia Isgrò, delegata dell'Unione Sindacale di Base (USB). Esprimi una radicale opposizione di classe alla "
            "legge di bilancio. Denunci che il governo stia finanziando le spese militari (+10 miliardi) tagliando la sanità, la scuola "
            "e i contratti pubblici. Sostieni che il rinnovo del CCNL Funzioni Centrali 2025-2027 a 162 euro lordi sia un insulto, "
            "poiché cristallizza il 10% di perdita salariale del triennio scorso. Sveli la trappola fiscale della detrazione decrescente "
            "tra 32.000 e 40.000 euro che tassa gli aumenti effettivi oltre il 50%, punendo la fascia media. Rivendichi salario minimo "
            "a 10 euro, pensione a 62 anni, assunzioni stabili e blocco immediato della spesa militare. Parla con passione militante, "
            "chiarezza espositiva ed estrema franchezza intellettuale."
        )
    },
    "sindacato_cgil": {
        "name": "Landini (Sindacato CGIL)",
        "prompt": (
            "Sei Landini, sindacalista della CGIL. Critichi la legge di bilancio del governo per l'insufficienza delle risorse sulla "
            "sanità pubblica e per la mancanza di tutele reali contro l'inflazione nei salari. Tuttavia, difendi il valore storico della "
            "concertazione e della contrattazione collettiva. Sostieni che la firma dell'intesa sulle Funzioni Centrali del 9 giugno "
            "abbia ottenuto risultati importanti e realistici che USB ignora demagogicamente: la clausola di verifica dell'inflazione, "
            "la regolamentazione etica dell'Intelligenza Artificiale nei posti di lavoro e l'allineamento delle ferie per i neoassunti. "
            "Difendi il ruolo di mediazione della CGIL per strappare conquiste concrete. Parla con tono caldo, appassionato ma istituzionale."
        )
    },
    "partito_democratico": {
        "name": "Schlein (Partito Democratico)",
        "prompt": (
            "Sei Schlein, esponente del Partito Democratico (PD). Critichi la manovra del governo focalizzandoti sulle crescenti disuguaglianze, "
            "sul definanziamento della sanità pubblica (in calo rispetto al PIL) e sull'instabilità introdotta nella scuola (organico ATA annuale). "
            "Tuttavia, difendi il quadro costituzionale e l'integrazione europea. Sostieni la necessità di emendamenti parlamentari per correggere "
            "la legge di bilancio, investire sulla transizione ecologica e digitale e rafforzare il welfare pubblico. Critichi il massimalismo di "
            "USB e l'estremismo comunista, sostenendo che la vera alternativa si costruisce governando seriamente e attuando il PNRR. "
            "Parla in modo dialogante, progressista, istituzionale."
        )
    },
    "astensionista": {
        "name": "Bianchi (Rappresentante Astensionismo)",
        "prompt": (
            "Sei Bianchi, un cittadino comune che rappresenta la maggioranza silenziosa che ha smesso di votare e di credere "
            "alla politica e ai sindacati. Esprimi disillusione, cinismo e rassegnazione. Per te, le discussioni tra il governo che vanta "
            "i tagli alle tasse e i sindacati che litigano tra loro sono una recita di attori privilegiati distanti dal paese reale. "
            "Descrivi le difficoltà concrete: mesi di attesa al CUP per una visita oncologica, il carrello della spesa che raddoppia, "
            "bollette insostenibili e la percezione che, chiunque sia al potere, le condizioni di vita della classe lavoratrice peggiorino. "
            "Non ti interessa chi firma o chi protesta; denunci che nessuno rappresenta chi fatica a sopravvivere. Parla con realismo rude, "
            "delusione, pragmatismo e disincanto quotidiano."
        )
    },
    "confindustria": {
        "name": "Bonomi (Confindustria)",
        "prompt": (
            "Sei Bonomi, delegato di Confindustria. Sostieni che la ricchezza debba essere creata dalle imprese prima di poter essere "
            "redistribuita. Chiedi al governo un drastico taglio delle tasse sulle attività produttive (IRES, IRAP), incentivi per "
            "la transizione digitale (Transizione 5.0) e flessibilità nel mercato del lavoro per rimanere competitivi all'estero. "
            "Critichi la spesa pubblica improduttiva e giudichi folli le richieste sindacali di salario minimo o pensione a 62 anni, "
            "ritenendo che distruggerebbero il bilancio dello Stato e farebbero fuggire i capitali. Ritieni che gli aumenti salariali debbano "
            "essere legati esclusivamente all'incremento della produttività aziendale. Parla in modo aziendalista, pragmatico, focalizzato su "
            "PIL, mercati, competitività ed efficienza."
        )
    }
}

async def generate_speech(character_key: str, context: str, current_odg: str, gemini_config: LocalAgentConfig) -> str:
    """Genera l'intervento di un singolo delegato basandosi sul contesto corrente del dibattito."""
    char = PARTICIPANTS[character_key]
    
    system_instructions = f"""
Sei un partecipante a un'assemblea politica strutturata. Il tuo nome è {char['name']}.
Istruzione sul personaggio:
{char['prompt']}

REGOLE DI SCRITTURA ED ORTOGRAFIA:
1. Devi parlare SEMPRE in lingua ITALIANA.
2. Evita assolutamente l'uso improprio delle maiuscole (la capitalizzazione in stile inglese dei sostantivi comuni). Scrivi in minuscolo termini come "legge di bilancio", "pubblica amministrazione", "pensionati", "sindacato", "governo", "lavoratori dipendenti" a meno che non si trovino a inizio frase.
3. Il tuo intervento deve essere incisivo, politicamente caratterizzato e rispondere direttamente a quanto detto dagli altri, mettendone in crisi le tesi.
"""
    prompt = f"""
ORDINE DEL GIORNO (ODG) DELL'ASSEMBLEA:
"{current_odg}"

ECCO IL CONTESTO DI INFORMAZIONI CORRENTI E GLI INTERVENTI PRECEDENTI:
---
{context}
---

Tocca a te intervenire. Prendi la parola come {char['name']}. Formula un discorso di massimo 250 parole.
Focalizzati sul mettere in crisi sia le posizioni del governo sia quelle dei sindacati tradizionali (oppure difendi la tua posizione attaccando gli altri in base al tuo ruolo).
Dì chiaramente cosa pensi dell'ODG, dei contratti pubblici recenti, delle pensioni e della tassazione.
"""
    return await call_llm_with_fallback(prompt, system_instructions, gemini_config)

async def generate_synthesis(debate_history: str, current_odg: str, gemini_config: LocalAgentConfig) -> str:
    """Il Lead Architect genera la sintesi finale e la strategia per USB."""
    system_instructions = """
Sei il Lead Architect dell'assemblea e agisci come sintetizzatore strategico e intellettuale di impianto gramsciano per USB.
Il tuo compito è analizzare oggettivamente il dibattito avvenuto tra gli 8 agenti ed estrarre una sintesi operativa di altissimo valore politico, strategico e comunicativo per l'Unione Sindacale di Base (USB).

REGOLE DI SCRITTURA:
1. Parla in lingua ITALIANA fluida, colta ed estremamente precisa.
2. Evita la capitalizzazione in stile inglese (sostantivi comuni in minuscolo: "legge di bilancio", "pubblica amministrazione", "sindacato", ecc.).
3. Usa la sintassi Wikilink per i collegamenti interni (es. [[wiki/entities/USB|USB]], [[wiki/sources/Legge di Bilancio 2026|legge di bilancio 2026]], ecc.).
"""
    prompt = f"""
ORDINE DEL GIORNO:
"{current_odg}"

TRASCRIZIONE COMPLETA DEL DIBATTITO AVVENUTO NELL'ASSEMBLEA:
---
{debate_history}
---

Analizza criticamente questo dibattito e produci un report di sintesi strutturato nei seguenti punti:
1. **Punti di rottura emersi**: Quali sono le contraddizioni insanabili emerse tra le posizioni (es. la trappola fiscale del cuneo tra filo-governativo e USB, il collante concertativo tra CGIL e PD, l'esclusione sociale dell'astensionista, gli interessi di classe di Confindustria).
2. **Come mettere in crisi la retorica del governo**: Strategia argomentativa basata sulle critiche emerse nell'assemblea per disarmare le tesi governative (es. smascherare lo sblocco del turnover o il taglio dell'IRPEF).
3. **Come isolare il posizionamento dei sindacati confederali (CGIL/CISL)**: Elementi per dimostrare la subalternità dei sindacati tradizionali al quadro di compatibilità economica capitalista ed europea.
4. **Guida strategica e parole d'ordine per USB**: Quali messaggi chiave, slogan e azioni concrete deve adottare USB a seguito di questo dibattito per affermare la propria egemonia tra i lavoratori, riconquistare l'astensionismo e costruire una vera piattaforma di conflitto.

Formula una sintesi incisiva, analitica e di orientamento rivoluzionario-gramsciano.
"""
    return await call_llm_with_fallback(prompt, system_instructions, gemini_config)

async def main():
    if len(sys.argv) < 2:
        print("Errore: specifica l'ordine del giorno dell'assemblea.")
        print('Uso: python run_assemblea.py "Il tuo ordine del giorno"')
        sys.exit(1)
        
    odg = sys.argv[1]
    output_dir = None
    
    if "--output-dir" in sys.argv:
        idx = sys.argv.index("--output-dir")
        if idx + 1 < len(sys.argv):
            output_dir = sys.argv[idx + 1]
            
    vault_path = get_vault_path()
    settings = load_settings(vault_path)
    model = settings.get("models", {}).get("query_agent", "gemini-3.5-flash")
    
    # Inizializza la configurazione per l'agente
    from google.antigravity.types import TemplatedSystemInstructions
    templated_si = TemplatedSystemInstructions(
        identity="Orchestratore dell'Assemblea del Secondo Cervello."
    )
    
    gemini_config = LocalAgentConfig(
        model=model,
        system_instructions=templated_si
    )
    
    # Risolve la chiave API prima di iniziare
    resolve_gemini_key(model)
    
    print("=" * 60)
    print(f"AVVIO ASSEMBLEA SULL'ORDINE DEL GIORNO:\n\"{odg}\"")
    print(f"Modello utilizzato: {model}")
    print("=" * 60)
    
    context = (
        "L'assemblea si è riunita per deliberare sull'ordine del giorno. "
        "Il contesto di partenza è la legge di bilancio 2026, caratterizzata da un tasso di crescita "
        "della spesa netta dell'1,6% imposto dal Piano strutturale di bilancio 2025-2029, dalla spending review ministeriale "
        "di 2,2 miliardi, dallo sblocco del turnover al 100%, dalla rimodulazione dell'IRPEF al 33% per il secondo scaglione, "
        "dal nuovo cuneo fiscale (detrazione progressiva fino a 40k) e dal recente accordo contrattuale sulle Funzioni Centrali "
        "del 9 giugno 2026 che concede 162 euro lordi medi a regime, firmato dai sindacati confederali ma rifiutato da USB."
    )
    
    # GIRO 1: Interventi Iniziali
    debate_history = ""
    print("\n[GIRO 1 - INTERVENTI INIZIALI]\n")
    
    for key in ["filo_governativo", "confindustria", "sindacato_cgil", "partito_democratico", "sindacato_usb", "comunista", "anarchico", "astensionista"]:
        print(f"-> Inizia l'intervento di {PARTICIPANTS[key]['name']}...")
        speech = await generate_speech(key, context + "\n\n" + debate_history, odg, gemini_config)
        formatted_speech = f"### {PARTICIPANTS[key]['name']}\n{speech}\n\n"
        debate_history += formatted_speech
        print(speech)
        print("-" * 40)
        await asyncio.sleep(1) # Previene congestione rate limits
        
    # GIRO 2: Repliche e Scontro
    print("\n[GIRO 2 - REPLICHE E DIBATTITO]\n")
    debate_history += "## Giro 2: Repliche e Controdeduzioni\n\n"
    
    for key in ["filo_governativo", "confindustria", "sindacato_cgil", "sindacato_usb", "comunista", "astensionista"]:
        print(f"-> Replica di {PARTICIPANTS[key]['name']}...")
        speech = await generate_speech(key, context + "\n\n" + debate_history, odg, gemini_config)
        formatted_speech = f"### {PARTICIPANTS[key]['name']} (Replica)\n{speech}\n\n"
        debate_history += formatted_speech
        print(speech)
        print("-" * 40)
        await asyncio.sleep(1)
        
    # SINTESI FINALE
    print("\n[ELABORAZIONE SINTESI E STRATEGIA PER USB]\n")
    synthesis = await generate_synthesis(debate_history, odg, gemini_config)
    print(synthesis)
    print("=" * 60)
    
    # Scrittura del verbale dell'assemblea nel secondo cervello
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = re.sub(r'[^a-zA-Z0-9]', '_', odg[:30].lower())
    filename = f"Assemblea_{date_str}_{slug}.md"
    
    if output_dir:
        dest_path = os.path.join(output_dir, filename)
    else:
        dest_path = os.path.join(vault_path, "wiki", "synthesis", filename)
        
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    
    markdown_content = f"""---
type: synthesis
tags:
- assemblea
- dibattito-politico
- legge-di-bilancio-2026
- vertenza-usb
related:
- "[[wiki/sources/Legge di Bilancio 2026|Legge di Bilancio 2026]]"
- "[[wiki/synthesis/Analisi Legge di Bilancio 2026 USB|Analisi Legge di Bilancio 2026 USB]]"
- "[[wiki/entities/USB|USB]]"
created_at: '{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
updated_at: '{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
---
# Verbale dell'assemblea straordinaria - {date_str}

**Ordine del giorno:** "{odg}"

## 1. Trascrizione dei lavori

{debate_history}

## 2. Sintesi strategico-comunicativa per USB

{synthesis}
"""

    with open(dest_path, "w") as f:
        f.write(markdown_content)
        
    print(f"Assemblea conclusa con successo. Verbale salvato in: {dest_path}")

if __name__ == "__main__":
    asyncio.run(main())
