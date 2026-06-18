import os
import sys
import subprocess
import shutil
import asyncio
import re
from engine.tools.vault_tools import get_vault_path
from engine.utils.markdown import parse_markdown

# Contenuto di default per il file .graphifyignore
GRAPHIFY_IGNORE_CONTENT = """# File di esclusione per Graphify (Scenario B - codebase)
.git
.git/
.git/**
.venv
.venv/
.venv/**
.obsidian
.obsidian/
.obsidian/**
__pycache__
__pycache__/
second_brain.egg-info
second_brain.egg-info/
.pytest_cache
.pytest_cache/
graphify-out
graphify-out/
graphify-out/**
raw
raw/
raw/**
journal
journal/
journal/**
CRM
CRM/
CRM/**
Meetings
Meetings/
Meetings/**
People
People/
People/**
Microthemes
Microthemes/
Microthemes/**
Transcripts
Transcripts/
Transcripts/**
images
images/
fonts
fonts/
wiki/sources
wiki/sources/
wiki/sources/**
wiki/entities
wiki/entities/
wiki/entities/**
log.md
processed_files.json
*.DS_Store
"""

# Contenuto per il Git Hook post-commit
GIT_HOOK_CONTENT = """#!/bin/bash
# Hook post-commit generato automaticamente da Secondo Cervello
# Esegue la build dei grafi Graphify in background dopo ogni commit

# Rileva percorso assoluto del progetto
PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

# Esegui la build dei grafi in background
if [ -f "$PROJECT_DIR/.venv/bin/python" ]; then
    "$PROJECT_DIR/.venv/bin/python" -m engine.main graphify > /dev/null 2>&1 &
else
    python3 -m engine.main graphify > /dev/null 2>&1 &
fi
"""

def get_graphify_executable() -> str:
    """Restituisce il percorso dell'eseguibile graphify."""
    vault_path = get_vault_path()
    
    # Cerca all'interno dell'ambiente virtuale locale
    local_graphify = os.path.join(vault_path, ".venv", "bin", "graphify")
    if os.path.exists(local_graphify):
        return local_graphify
        
    # Cerca nel path globale del sistema
    global_graphify = shutil.which("graphify")
    if global_graphify:
        return global_graphify
        
    # Fallback all'interprete python con -m graphify se disponibile
    return "python -m graphify"

def build_wiki_graph() -> bool:
    """Scenario A: Costruisce il grafo semantico della cartella wiki/."""
    vault_path = get_vault_path()
    wiki_path = os.path.join(vault_path, "wiki")
    out_dir = os.path.join(vault_path, "graphify-out", "wiki")
    
    if not os.path.exists(wiki_path):
        print(f"[Graphify] Cartella wiki non trovata in {wiki_path}. Salto build.")
        return False
        
    os.makedirs(out_dir, exist_ok=True)
    
    # Innalza il limite dei nodi per il rendering HTML del grafo
    os.environ["GRAPHIFY_VIZ_NODE_LIMIT"] = "15000"
    
    # Risolve la chiave API di Gemini prima di lanciare Graphify
    try:
        from engine.utils.llm_fallback import resolve_gemini_key
        resolve_gemini_key("gemini-2.5-flash")
    except Exception as e:
        print(f"[Graphify] Avviso: errore rotazione chiavi: {e}")
        
    executable = get_graphify_executable()
    print(f"[Graphify] Avvio build grafo Wiki (Scenario A)...")
    
    # Configura backend commerciale Gemini per massima velocità e precisione
    backend_args = ["--backend", "gemini", "--model", "gemini-2.5-flash", "--max-concurrency", "1", "--token-budget", "2500"]
    print("[Graphify] Utilizzo del backend Gemini (gemini-2.5-flash) con concorrenza 1 e budget 2500.")
    
    try:
        if " -m " in executable or "python" in executable:
            # Esegui tramite modulo python
            python_exe = os.path.join(vault_path, ".venv", "bin", "python")
            if not os.path.exists(python_exe):
                python_exe = sys.executable
            cmd = [python_exe, "-m", "graphify", "wiki/"] + backend_args
        else:
            cmd = [executable, "wiki/"] + backend_args
            
        print(f"[Graphify] Esecuzione comando: {' '.join(cmd)}")
        # Passa tutte le chiavi disponibili a graphify per consentire la rotazione interna
        from engine.utils.llm_fallback import get_gemini_keys
        env = os.environ.copy()
        keys_pool = get_gemini_keys()
        if keys_pool:
            env["GEMINI_API_KEY"] = ",".join(keys_pool)
        res = subprocess.run(cmd, cwd=vault_path, env=env)
        if res.returncode == 0:
            print("[Graphify] Grafo Wiki costruito con successo.")
            return True
        else:
            print(f"[Graphify] Errore build Wiki: codice di uscita {res.returncode}")
            return False
    except Exception as e:
        print(f"[Graphify] Eccezione durante la build del grafo Wiki: {e}")
        return False

def build_codebase_graph() -> bool:
    """Scenario B: Costruisce il grafo della codebase del progetto."""
    vault_path = get_vault_path()
    out_dir = os.path.join(vault_path, "graphify-out", "codebase")
    
    # Assicurati che esista il file .graphifyignore
    ignore_file = os.path.join(vault_path, ".graphifyignore")
    if not os.path.exists(ignore_file):
        with open(ignore_file, "w", encoding="utf-8") as f:
            f.write(GRAPHIFY_IGNORE_CONTENT)
            
    os.makedirs(out_dir, exist_ok=True)
    
    # Innalza il limite dei nodi per il rendering HTML del grafo
    os.environ["GRAPHIFY_VIZ_NODE_LIMIT"] = "15000"
    
    # Risolve la chiave API di Gemini prima di lanciare Graphify
    try:
        from engine.utils.llm_fallback import resolve_gemini_key
        resolve_gemini_key("gemini-2.5-flash")
    except Exception as e:
        print(f"[Graphify] Avviso: errore rotazione chiavi: {e}")
        
    executable = get_graphify_executable()
    print(f"[Graphify] Avvio build grafo Codebase (Scenario B)...")
    
    # Configura backend commerciale Gemini per massima velocità e precisione
    backend_args = ["--backend", "gemini", "--model", "gemini-2.5-flash", "--max-concurrency", "1", "--token-budget", "2500"]
    
    try:
        if " -m " in executable or "python" in executable:
            python_exe = os.path.join(vault_path, ".venv", "bin", "python")
            if not os.path.exists(python_exe):
                python_exe = sys.executable
            cmd = [python_exe, "-m", "graphify", "engine/"] + backend_args
        else:
            cmd = [executable, "engine/"] + backend_args
            
        print(f"[Graphify] Esecuzione comando: {' '.join(cmd)}")
        # Passa tutte le chiavi disponibili a graphify per consentire la rotazione interna
        from engine.utils.llm_fallback import get_gemini_keys
        env = os.environ.copy()
        keys_pool = get_gemini_keys()
        if keys_pool:
            env["GEMINI_API_KEY"] = ",".join(keys_pool)
        res = subprocess.run(cmd, cwd=vault_path, env=env)
        if res.returncode == 0:
            print("[Graphify] Grafo Codebase costruito con successo.")
            return True
        else:
            print(f"[Graphify] Errore build Codebase: codice di uscita {res.returncode}")
            return False
    except Exception as e:
        print(f"[Graphify] Eccezione durante la build del grafo Codebase: {e}")
        return False

def build_all() -> bool:
    """Costruisce entrambi i grafi (Wiki e Codebase)."""
    ok_wiki = build_wiki_graph()
    ok_code = build_codebase_graph()
    return ok_wiki and ok_code

async def run_graphify_async():
    """Innesca la build asincrona non bloccante dei grafi (Scenario A + B)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, build_all)

def setup_graphify_integration() -> bool:
    """Esegue l'autoconfigurazione di Graphify (cartelle, impostazioni, git hooks, build iniziale)."""
    vault_path = get_vault_path()
    print("[Graphify] Inizio autoconfigurazione di Graphify...")
    
    # 1. Creazione cartelle di output
    os.makedirs(os.path.join(vault_path, "wiki", "graphify-out"), exist_ok=True)
    os.makedirs(os.path.join(vault_path, "engine", "graphify-out"), exist_ok=True)
    
    # 2. Configurazione file .graphifyignore
    ignore_file = os.path.join(vault_path, ".graphifyignore")
    with open(ignore_file, "w", encoding="utf-8") as f:
        f.write(GRAPHIFY_IGNORE_CONTENT)
    print("[Graphify] Configurato file .graphifyignore.")
        
    # 3. Configurazione Git Hook post-commit
    git_dir = os.path.join(vault_path, ".git")
    if os.path.exists(git_dir):
        hooks_dir = os.path.join(git_dir, "hooks")
        os.makedirs(hooks_dir, exist_ok=True)
        
        hook_path = os.path.join(hooks_dir, "post-commit")
        with open(hook_path, "w", encoding="utf-8") as f:
            f.write(GIT_HOOK_CONTENT)
            
        # Rendi eseguibile su mac/linux
        try:
            os.chmod(hook_path, 0o755)
            print(f"[Graphify] Git hook post-commit configurato con successo in {hook_path}.")
        except Exception as chmod_err:
            print(f"[Graphify] Errore nell'impostazione dei permessi sul git hook: {chmod_err}")
    else:
        print("[Graphify] Avviso: cartella .git non trovata. Hook Git non configurato.")
        
    # 4. Aggiornamento automatico di settings.md con il timing
    settings_file = os.path.join(vault_path, "settings.md")
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                content = f.read()
                
            # Parse YAML per controllare se c'è già il timing
            fm, body = parse_markdown(content)
            timing = fm.get("timing", {})
            
            if "graphify_update" not in timing:
                # Modifichiamo il file aggiungendo la chiave sotto timing:
                # Per preservare commenti e formattazione, cerchiamo di fare una sostituzione mirata nel testo
                lines = content.splitlines()
                timing_index = -1
                for i, line in enumerate(lines):
                    if line.strip().startswith("timing:"):
                        timing_index = i
                        break
                        
                if timing_index != -1:
                    # Inseriamo la nuova impostazione subito sotto 'timing:'
                    indent = ""
                    if timing_index + 1 < len(lines):
                        match = re.match(r"^(\s+)", lines[timing_index + 1])
                        if match:
                            indent = match.group(1)
                    if not indent:
                        indent = "  "
                        
                    lines.insert(timing_index + 1, f'{indent}graphify_update: "3600"                  # Ogni ora (3600 secondi)')
                    new_content = "\n".join(lines) + "\n"
                    with open(settings_file, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    print("[Graphify] Aggiunto graphify_update a settings.md.")
        except Exception as settings_err:
            print(f"[Graphify] Errore nell'aggiornamento di settings.md: {settings_err}")
            
    # 5. Prima compilazione del grafo
    print("[Graphify] Esecuzione della prima build iniziale dei grafi...")
    build_all()
    
    print("[Graphify] Autoconfigurazione completata.")
    return True
