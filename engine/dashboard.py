import os
import sys
import re
import json
import asyncio
import subprocess
from typing import Optional, List
from fastapi import FastAPI, Request, Response, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from engine.ontology_agent import rollback_ontology_proposal, confirm_ontology_proposal

class PromptsEditRequest(BaseModel):
    prompts: dict

class SettingsEditRequest(BaseModel):
    settings: dict
    env: Optional[dict] = None

class AutomationScheduleRequest(BaseModel):
    schedule: str

class CustomAutomationCreateRequest(BaseModel):
    key: str
    name: str
    description: str
    timing: str
    command_args: List[str]
    agent_prompt: Optional[str] = None


# Carica le variabili d'ambiente prima di tutto
load_dotenv()

# Add root folder to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.tools.vault_tools import get_vault_path, list_unprocessed_raw, search_wiki
from engine.utils.markdown import parse_markdown
from engine.query_agent import query_agent_answer, get_second_brain_statistics
from engine.watcher import watch_vault_changes
from engine.tools.mail_idle import start_imap_idle_listeners
from engine.tools.notion_tasks import create_notion_task
from engine.tools.notion_calendar import create_notion_calendar_event

# FastMCP Server Import
try:
    from mcp.server.fastmcp import FastMCP
    mcp_server = FastMCP("Second Brain")
except ImportError:
    mcp_server = None

import datetime
try:
    from croniter import croniter
except ImportError:
    croniter = None

# --- SQLite Database Scheduler Helpers ---
from engine.db.connection import db_session, init_db
from engine.db.models import ScheduledJob, ProceduralConfig

def sync_scheduled_jobs_to_db():
    """
    Popola la tabella scheduled_jobs con i compiti di default se vuota.
    """
    with db_session() as session:
        try:
            count = session.query(ScheduledJob).count()
        except Exception:
            init_db()
            count = 0
            
        if count == 0:
            defaults = [
                ScheduledJob(name="Sincronizzazione ed ingestione", schedule_type="time", schedule_value="10:00", target_action="ingest", is_active=1),
                ScheduledJob(name="Riflessione settimanale", schedule_type="time", schedule_value="21:00", target_action="reflect", is_active=1),
                ScheduledJob(name="Briefing pre-evento", schedule_type="interval", schedule_value="5", target_action="briefing", is_active=1),
                ScheduledJob(name="Dream mode notturna", schedule_type="time", schedule_value="03:00", target_action="dream", is_active=1),
                ScheduledJob(name="Aggiornamento grafi Graphify", schedule_type="interval", schedule_value="60", target_action="graphify", is_active=1)
            ]
            session.add_all(defaults)
            session.commit()
            print("[SCHEDULER] Inseriti compiti di default in SQLite.")

def sync_db_to_settings_md():
    vault_path = get_vault_path()
    settings_path = os.path.join(vault_path, "settings.md")
    if not os.path.exists(settings_path):
        return
        
    from engine.utils.markdown import parse_markdown, to_markdown
    
    with db_session() as session:
        jobs = session.query(ScheduledJob).all()
        
    with open(settings_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    fm, body = parse_markdown(content)
    if "timing" not in fm:
        fm["timing"] = {}
    if "custom_automations" not in fm:
        fm["custom_automations"] = {}
        
    for job in jobs:
        key = job.target_action
        if key == "ingest":
            key = "sync_and_ingest"
        elif key == "reflect":
            key = "weekly_reflection"
        elif key == "graphify":
            key = "graphify_update"
            
        val = job.schedule_value
        if job.schedule_type == "interval" and val.isdigit():
            val = str(int(val) * 60)
            
        if job.is_active == 0:
            fm["timing"][key] = ""
        else:
            fm["timing"][key] = val
            
        is_builtin = job.target_action in ("ingest", "reflect", "briefing", "dream", "graphify")
        if not is_builtin:
            fm["custom_automations"][key] = {
                "name": job.name,
                "description": f"Automazione custom per {job.name}",
                "timing_key": key,
                "command_args": job.target_action.split()
            }
            
    new_content = to_markdown(fm, body)
    with open(settings_path, "w", encoding="utf-8") as f:
        f.write(new_content)

# --- Unified Background Scheduler ---
class ScheduledTask:
    def __init__(self, name: str, command_args: List[str], timing_key: str):
        self.name = name
        self.command_args = command_args
        self.timing_key = timing_key
        self.last_run: Optional[datetime.datetime] = None
        self.next_run: Optional[datetime.datetime] = None
        self.interval_seconds: Optional[int] = None
        self.last_expr: Optional[str] = None
        
    def update_schedule(self, timing_dict: dict, now: datetime.datetime, force_update: bool = False):
        val = timing_dict.get(self.timing_key)
        if not val:
            self.next_run = None
            self.interval_seconds = None
            self.last_expr = None
            return
            
        val_str = str(val).strip()
        if val_str == self.last_expr and not force_update and self.next_run is not None:
            return
            
        self.last_expr = val_str
        
        if val_str.isdigit():
            self.interval_seconds = int(val_str)
            if self.last_run is None:
                self.next_run = now + datetime.timedelta(seconds=self.interval_seconds)
            else:
                self.next_run = self.last_run + datetime.timedelta(seconds=self.interval_seconds)
        else:
            if croniter is None:
                print(f"[SCHEDULER] Errore: libreria 'croniter' non disponibile. Impossibile pianificare {self.name}.", flush=True)
                self.next_run = None
                return
            try:
                cron = croniter(val_str, now)
                self.next_run = cron.get_next(datetime.datetime)
                self.interval_seconds = None
            except Exception as e:
                print(f"[SCHEDULER] Errore parsing cron '{val_str}' per {self.name}: {e}", flush=True)
                self.next_run = None
                
    def should_run(self, now: datetime.datetime) -> bool:
        if not self.next_run:
            return False
        return now >= self.next_run

scheduler_tasks = [
    ScheduledTask("Sincronizzazione ed ingestione", ["ingest"], "sync_and_ingest"),
    ScheduledTask("Riflessione settimanale", ["reflect"], "weekly_reflection"),
    ScheduledTask("Briefing pre-evento", ["briefing"], "briefing"),
    ScheduledTask("Dream mode notturna", ["dream"], "dream"),
    ScheduledTask("Aggiornamento grafi Graphify", ["graphify"], "graphify_update")
]

async def run_task_subprocess_by_name(job_name: str, target_action: str):
    vault_path = get_vault_path()
    python_exe = os.path.join(vault_path, ".venv", "bin", "python")
    if not os.path.exists(python_exe):
        python_exe = sys.executable
        
    import shlex
    cmd_args = shlex.split(target_action)
    args = [python_exe, "-u", "-m", "engine.main"] + cmd_args
    
    msg = f"[SCHEDULER] Avvio compito: {job_name} ({' '.join(args)})..."
    print(msg, flush=True)
    manager.write_log(msg)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=vault_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="ignore").strip()
            print(f"[{job_name}] {line}", flush=True)
            manager.write_log(f"[{job_name}] {line}")
            
        await proc.wait()
        msg_end = f"[SCHEDULER] Compito completato: {job_name} con codice {proc.returncode}"
        print(msg_end, flush=True)
        manager.write_log(msg_end)
    except Exception as e:
        err_msg = f"[SCHEDULER] Errore nell'esecuzione del compito {job_name}: {e}"
        print(err_msg, flush=True)
        manager.write_log(err_msg)

async def run_task_subprocess(task: ScheduledTask):
    await run_task_subprocess_by_name(task.name, " ".join(task.command_args))

async def run_scheduler_loop():
    print("[SCHEDULER] Avvio del ciclo dello scheduler universale basato su SQLite...", flush=True)
    await asyncio.sleep(5)
    
    # Assicura che i compiti di default siano sincronizzati nel DB
    try:
        sync_scheduled_jobs_to_db()
    except Exception as e:
        print(f"[SCHEDULER] Errore nell'inizializzazione dei job di default: {e}", flush=True)
        
    while True:
        try:
            from engine.db.connection import db_session
            from engine.db.models import ScheduledJob
            import time
            
            now = datetime.datetime.now()
            now_ts = now.timestamp()
            
            jobs_to_run = []
            
            with db_session() as session:
                active_jobs = session.query(ScheduledJob).filter(ScheduledJob.is_active == 1).all()
                for job in active_jobs:
                    should_run = False
                    
                    if job.last_run is None:
                        should_run = True
                    else:
                        last_run_dt = datetime.datetime.fromtimestamp(job.last_run)
                        
                        if job.schedule_type == "interval":
                            try:
                                interval_minutes = int(job.schedule_value)
                            except Exception:
                                interval_minutes = 60
                            if now_ts - job.last_run >= interval_minutes * 60:
                                should_run = True
                                
                        elif job.schedule_type == "time":
                            try:
                                hour, minute = map(int, job.schedule_value.split(":"))
                                target_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                
                                if now >= target_today and last_run_dt < target_today:
                                    should_run = True
                            except Exception as e:
                                print(f"[SCHEDULER] Errore parsing time '{job.schedule_value}' per job #{job.id}: {e}")
                                
                    if should_run:
                        jobs_to_run.append((job.id, job.name, job.target_action))
                        job.last_run = now_ts
                        
                if jobs_to_run:
                    session.commit()
                    
            for job_id, job_name, target_action in jobs_to_run:
                asyncio.create_task(run_task_subprocess_by_name(job_name, target_action))
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[SCHEDULER] Errore imprevisto nel loop: {e}", flush=True)
            
        await asyncio.sleep(10)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Carica la cache del grafo da disco all'avvio
    load_graph_cache_from_disk()
    
    # Avvia la pre-generazione asincrona del grafo in background
    asyncio.create_task(asyncio.to_thread(build_graph_data, force_update=True))
    
    # Avvia la pre-generazione asincrona delle statistiche in background
    asyncio.create_task(asyncio.to_thread(get_second_brain_statistics))
    
    # Avvia lo scheduler in background
    scheduler_task = asyncio.create_task(run_scheduler_loop())
    
    # Avvia il file watcher per modifiche locali
    watcher_task = asyncio.create_task(watch_vault_changes(manager))
    
    # Avvia i listener IMAP IDLE per le email
    idle_tasks = []
    try:
        idle_tasks = await start_imap_idle_listeners(manager)
    except Exception as e:
        print(f"[DASHBOARD] Errore nell'avvio dei listener IMAP IDLE: {e}", flush=True)
        
    yield
    
    # Cancellazione e pulizia all'arresto
    scheduler_task.cancel()
    watcher_task.cancel()
    for task in idle_tasks:
        task.cancel()
        
    # Attesa terminazione
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    try:
        await watcher_task
    except asyncio.CancelledError:
        pass
    for task in idle_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass

app = FastAPI(title="Secondo Cervello - Dashboard", lifespan=lifespan)

from fastapi.staticfiles import StaticFiles
app.mount("/fonts", StaticFiles(directory=os.path.join(get_vault_path(), "fonts")), name="fonts")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")), name="static")

class TimeScheduleRequest(BaseModel):
    time: str  # Format: "HH:MM"

class TaskCreateRequest(BaseModel):
    title: str
    due_date: Optional[str] = None
    status: Optional[str] = "To Do"
    category: Optional[str] = "General"

class CalendarEventCreateRequest(BaseModel):
    title: str
    start_time: str
    end_time: Optional[str] = None
    location: Optional[str] = None

class FileEditRequest(BaseModel):
    content: str

# --- Ingestion Process Manager ---
class IngestionManager:
    def __init__(self):
        self.process: Optional[asyncio.subprocess.Process] = None
        self.lock = asyncio.Lock()
        self.log_history: List[str] = []
        self.listeners: List[asyncio.Queue] = []
        self.max_history = 1000
        self.active_source: str = "none"

    async def start(self, source: Optional[str] = None) -> bool:
        async with self.lock:
            if self.is_running():
                return False
            
            vault_path = get_vault_path()
            python_exe = os.path.join(vault_path, ".venv", "bin", "python")
            if not os.path.exists(python_exe):
                python_exe = sys.executable  # Fallback
                
            self.log_history.clear()
            self.active_source = source or "all"
            
            args = [python_exe, "-u", "-m", "engine.main", "ingest"]
            if source and source != "all":
                args.extend(["--source", source])
                
            self.process = await asyncio.create_subprocess_exec(
                *args,
                cwd=vault_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                preexec_fn=os.setsid if os.name != 'nt' else None
            )
            
            # Start background reader task
            asyncio.create_task(self._read_output())
            return True

    def write_log(self, line: str):
        self.log_history.append(line)
        if len(self.log_history) > self.max_history:
            self.log_history.pop(0)
        for q in list(self.listeners):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(q.put_nowait, line)
                else:
                    q.put_nowait(line)
            except Exception:
                pass

    async def _read_output(self):
        # Read lines asynchronously
        while self.process and self.process.stdout:
            line_bytes = await self.process.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="ignore").strip()
            
            # Print to stdout too so it shows in server console
            print(f"[INGESTION] {line}", flush=True)
            self.write_log(f"[INGESTION] {line}")
                
        # Clean up process reference when complete
        async with self.lock:
            self.process = None
            self.active_source = "none"

    async def stop(self) -> bool:
        async with self.lock:
            if not self.is_running():
                return False
            try:
                import signal
                if os.name != 'nt':
                    # Send SIGTERM to the process group
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                else:
                    self.process.terminate()
                    
                # Wait up to 5 seconds
                for _ in range(50):
                    if self.process is None:
                        break
                    await asyncio.sleep(0.1)
                    
                # Force kill if still running
                if self.process is not None:
                    if os.name != 'nt':
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    else:
                        self.process.kill()
            except Exception as e:
                print(f"Errore nell'interrompere l'ingestione: {e}")
            finally:
                self.process = None
                self.active_source = "none"
            return True

    def is_running(self) -> bool:
        return self.process is not None

    def register_listener(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self.listeners.append(q)
        return q

    def unregister_listener(self, q: asyncio.Queue):
        if q in self.listeners:
            self.listeners.remove(q)

manager = IngestionManager()

# --- Helper Functions for Settings ---
def get_schedule_time() -> str:
    vault_path = get_vault_path()
    settings_path = os.path.join(vault_path, "settings.md")
    if not os.path.exists(settings_path):
        return "10:00"
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            content = f.read()
        fm, _ = parse_markdown(content)
        cron_str = fm.get("timing", {}).get("sync_and_ingest", "0 10 * * *")
        match = re.match(r'^(\d+)\s+(\d+)', cron_str.strip())
        if match:
            minute, hour = match.groups()
            return f"{int(hour):02d}:{int(minute):02d}"
    except Exception as e:
        print(f"Errore lettura orario schedulato: {e}")
    return "10:00"

def set_schedule_time(time_str: str) -> bool:
    try:
        hour, minute = time_str.split(":")
        cron_str = f"{int(minute)} {int(hour)} * * *"
        
        vault_path = get_vault_path()
        settings_path = os.path.join(vault_path, "settings.md")
        with open(settings_path, "r", encoding="utf-8") as f:
            content = f.read()
        fm, body = parse_markdown(content)
        if "timing" not in fm:
            fm["timing"] = {}
        fm["timing"]["sync_and_ingest"] = cron_str
        
        from engine.utils.markdown import to_markdown
        new_content = to_markdown(fm, body)
        with open(settings_path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        # Rigenera launchd plist
        python_exe = os.path.join(vault_path, ".venv", "bin", "python")
        subprocess.run([python_exe, "-m", "engine.plist_generator", "install"], cwd=vault_path)
        return True
    except Exception as e:
        print(f"Errore nel salvare l'orario: {e}")
        return False

# --- Graph Engine Backend ---
_graph_cache = None
_graph_cache_time = 0

def load_graph_cache_from_disk():
    global _graph_cache, _graph_cache_time
    import json
    try:
        vault_path = get_vault_path()
        cache_path = os.path.join(vault_path, "vault", "graph_cache.json")
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                _graph_cache = json.load(f)
            _graph_cache_time = os.path.getmtime(cache_path)
            print(f"[GRAPH] Cache caricata da disco ({len(_graph_cache.get('nodes', []))} nodi).", flush=True)
        else:
            print("[GRAPH] Cache su disco non trovata.", flush=True)
    except Exception as e:
        print(f"[GRAPH] Errore nel caricamento della cache da disco: {e}", flush=True)

def build_graph_data(force_update=False):
    global _graph_cache, _graph_cache_time
    import time
    import json
    if not force_update and _graph_cache and (time.time() - _graph_cache_time < 3600):
        return _graph_cache
        
    vault_path = get_vault_path()
    nodes = []
    links = []
    wiki_re = re.compile(r'\[\[(.*?)\]\]')
    folders = ["wiki", "CRM", "Meetings", "People", "journal", "Microthemes"]
    
    # Costruiamo una mappa per risolvere i link corti (solo nome file) nei percorsi relativi corretti
    short_to_rel_map = {}
    rel_path_set = set()
    file_metadata = {}
    raw_edges = []
    node_set = set()
    
    for folder in folders:
        abs_folder = os.path.join(vault_path, folder)
        if not os.path.exists(abs_folder): continue
        for root, _, files in os.walk(abs_folder):
            for file in files:
                if file.endswith(".md"):
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, vault_path).replace(".md", "")
                    node_id = rel_path
                    node_set.add(node_id)
                    rel_path_set.add(rel_path)
                    
                    basename = os.path.splitext(file)[0]
                    short_to_rel_map[basename.lower()] = rel_path
                    
                    # Raccogliamo anche metadati per colorare/personalizzare i nodi nel grafo
                    group = 1
                    if rel_path.startswith("wiki/concepts/"):
                        group = 2
                    elif rel_path.startswith("wiki/entities/"):
                        group = 3
                    elif rel_path.startswith("wiki/sources/Riunioni/") or rel_path.startswith("Meetings/"):
                        group = 6
                    elif rel_path.startswith("wiki/sources/"):
                        group = 4
                    elif rel_path.startswith("CRM/"):
                        group = 5
                    elif rel_path.startswith("journal/"):
                        group = 7
                        
                    file_metadata[rel_path] = {
                        "name": basename,
                        "group": group
                    }
                    
                    # Leggiamo il file per estrarre i link
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                            matches = wiki_re.findall(content)
                            for match in matches:
                                target = match.split("|")[0].strip()
                                raw_edges.append((node_id, target))
                    except:
                        pass

    # Risolviamo i link corti ed edges
    edges = []
    for source, target in raw_edges:
        resolved_target = target
        if "/" not in target and "\\" not in target:
            resolved_target = short_to_rel_map.get(target.lower(), target)
        edges.append({"source": source, "target": resolved_target})
        node_set.add(resolved_target)
        
    # Calcolo dei degree efficiente O(E)
    from collections import Counter
    degree_counter = Counter()
    for e in edges:
        degree_counter[e["source"]] += 1
        degree_counter[e["target"]] += 1
        
    for n in node_set:
        degree = degree_counter[n]
        meta = file_metadata.get(n, {
            "name": os.path.basename(n),
            "group": 1
        })
        nodes.append({
            "id": n,
            "name": meta["name"],
            "val": max(degree, 1),
            "group": meta["group"]
        })
        
    for e in edges:
        links.append({"source": e["source"], "target": e["target"]})
        
    _graph_cache = {"nodes": nodes, "links": links}
    _graph_cache_time = time.time()
    
    # Salva su disco
    try:
        cache_path = os.path.join(vault_path, "vault", "graph_cache.json")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(_graph_cache, f, ensure_ascii=False)
        print(f"[GRAPH] Cache salvata su disco: {cache_path}", flush=True)
    except Exception as e:
        print(f"[GRAPH] Errore nel salvataggio della cache su disco: {e}", flush=True)
        
    return _graph_cache


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[dict]] = None
    conversation_id: Optional[str] = None

# --- Web UI Routes ---
@app.get("/graph", response_class=HTMLResponse)
def read_graph():
    vault_path = get_vault_path()
    graphify_path = os.path.join(vault_path, "wiki", "graphify-out", "graph.html")
    if os.path.exists(graphify_path):
        try:
            with open(graphify_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"[DASHBOARD] Errore di lettura graphify wiki/graph.html: {e}", flush=True)
            
    template_path = os.path.join(os.path.dirname(__file__), "templates", "graph_chat.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    return "Template non trovato."

@app.get("/codebase-graph", response_class=HTMLResponse)
def read_codebase_graph():
    vault_path = get_vault_path()
    graphify_path = os.path.join(vault_path, "engine", "graphify-out", "graph.html")
    if os.path.exists(graphify_path):
        try:
            with open(graphify_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"Errore di lettura del grafo codebase: {e}"
    return "Grafo della codebase non ancora generato. Esegui 'make graphify' o attendi lo scheduler."

@app.get("/api/graph")
def get_graph():
    return build_graph_data()

@app.get("/api/wiki")
def get_wiki_page(path: str):
    """
    Ritorna il contenuto di una pagina wiki specificata dal percorso relativo o dal titolo,
    effettuando una ricerca tollerante all'interno delle cartelle del vault.
    """
    vault_path = get_vault_path()
    clean_path = os.path.normpath(path).replace("\\", "/").lstrip('/')
    if clean_path.startswith("..") or os.path.isabs(clean_path):
        raise HTTPException(status_code=400, detail="Percorso non valido.")
        
    # Se il file non esiste direttamente con estensione .md, proviamo a risolverlo
    if not clean_path.endswith(".md"):
        # 1. Controlla cartelle comuni
        possible_folders = ["wiki/entities", "wiki/concepts", "wiki/sources", "wiki/synthesis", "CRM", "Meetings", "journal", "Microthemes"]
        found = False
        for folder in possible_folders:
            test_path = os.path.join(folder, clean_path + ".md")
            if os.path.exists(os.path.join(vault_path, test_path)):
                clean_path = test_path
                found = True
                break
                
        # 2. Se non ancora trovato, cammina nel vault per cercare NoteName.md
        if not found:
            filename = os.path.basename(clean_path)
            if not filename.endswith(".md"):
                filename += ".md"
            for root, _, files in os.walk(vault_path):
                # Salta cartelle di configurazione/ambiente
                if any(x in root for x in [".git", ".venv", ".pytest_cache", "__pycache__"]):
                    continue
                if filename in files:
                    clean_path = os.path.relpath(os.path.join(root, filename), vault_path)
                    found = True
                    break
                    
    # Assicurati di aggiungere .md se manca ed è un file locale diretto
    if not clean_path.endswith(".md"):
        clean_path += ".md"
        
    abs_path = os.path.join(vault_path, clean_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail=f"Pagina wiki '{path}' non trovata.")
        
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
        fm, body = parse_markdown(content)
        title = os.path.splitext(os.path.basename(clean_path))[0]
        return {
            "path": clean_path,
            "title": title,
            "frontmatter": fm,
            "content": body
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class CreateWikiRequest(BaseModel):
    path: str
    title: str

@app.post("/api/wiki/create")
def create_wiki_page_endpoint(req: CreateWikiRequest):
    import time
    vault_path = get_vault_path()
    path = req.path
    title = req.title
    
    clean_path = os.path.normpath(path).replace("\\", "/").lstrip('/')
    if clean_path.startswith("..") or os.path.isabs(clean_path):
        raise HTTPException(status_code=400, detail="Percorso non valido.")
        
    if not clean_path.endswith(".md"):
        clean_path += ".md"
        
    if "/" not in clean_path:
        clean_path = os.path.join("wiki/concepts", clean_path)
        
    abs_path = os.path.join(vault_path, clean_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    
    if os.path.exists(abs_path):
        raise HTTPException(status_code=400, detail="La pagina esiste già.")
        
    try:
        content = f"""---
type: concept
created_at: '{time.strftime("%Y-%m-%d %H:%M:%S")}'
updated_at: '{time.strftime("%Y-%m-%d %H:%M:%S")}'
---
# {title}

Questa pagina è stata creata come segnaposto dal grafo del Secondo Cervello.
"""
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        global _graph_cache
        _graph_cache = None
        
        return {"status": "success", "path": clean_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def choose_emoji(query: str, answer: str) -> str:
    text = (query + " " + answer).lower()
    if any(k in text for k in ["verbale", "meeting", "riunione", "incontro", "call", "discussione", "meetings"]):
        return "📅"
    elif any(k in text for k in ["contatto", "crm", "persona", "people", "cliente", "collaboratore", "relazione", "profilo"]):
        return "👥"
    elif any(k in text for k in ["diario", "journal", "oggi", "ieri", "settimana", "riflessione", "personale"]):
        return "📝"
    elif any(k in text for k in ["sorgente", "source", "articolo", "web", "link", "url", "drive", "file", "documento", "pdf"]):
        return "📂"
    elif any(k in text for k in ["programma", "codice", "sviluppo", "python", "javascript", "html", "css", "bug", "errore"]):
        return "💻"
    return "🧠"

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    async def event_generator():
        ans_accumulator = []
        metadata = {"conversation_id": req.conversation_id}
        try:
            from engine.query_agent import query_agent_stream
            async for token in query_agent_stream(
                req.message, 
                history=req.history, 
                conversation_id=req.conversation_id,
                metadata=metadata
            ):
                ans_accumulator.append(token)
                yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
                
            full_ans = "".join(ans_accumulator)
            wiki_re = re.compile(r'\[\[(.*?)\]\]')
            cited = [m.split("|")[0].strip() for m in wiki_re.findall(full_ans)]
            emoji = choose_emoji(req.message, full_ans)
            
            # Send done event with metadata including actual conversation_id
            yield f"data: {json.dumps({
                'type': 'done', 
                'cited_nodes': cited, 
                'emoji': emoji,
                'conversation_id': metadata.get("conversation_id")
            })}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    paths: Optional[str] = Form(None)
):
    import shutil
    import json
    
    target_dir = os.path.join(get_vault_path(), "raw", "manual")
    os.makedirs(target_dir, exist_ok=True)
    
    relative_paths = []
    if paths:
        try:
            relative_paths = json.loads(paths)
        except Exception:
            pass
            
    saved_files = []
    for idx, file in enumerate(files):
        rel_path = None
        if relative_paths and idx < len(relative_paths):
            rel_path = relative_paths[idx]
            
        if rel_path:
            cleaned_rel = os.path.normpath(rel_path).lstrip(os.path.sep)
            if cleaned_rel.startswith("..") or os.path.isabs(cleaned_rel):
                cleaned_rel = os.path.basename(cleaned_rel)
            dest_path = os.path.join(target_dir, cleaned_rel)
        else:
            dest_path = os.path.join(target_dir, os.path.basename(file.filename))
            
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        saved_files.append(dest_path)
        
    # Avvia l'ingestore in background per elaborare i file manuali
    await manager.start(source="manual")
    
    return {
        "status": "success",
        "message": f"Caricati {len(saved_files)} file. Ingestione avviata.",
        "files": [os.path.basename(f) for f in saved_files]
    }


@app.get("/", response_class=HTMLResponse)
def read_root():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    return """
    <html>
        <head><title>Dashboard Errore</title></head>
        <body style="background:#0d0f12;color:#fff;font-family:sans-serif;padding:50px;text-align:center;">
            <h1>Dashboard Template non trovato!</h1>
            <p>Verifica che <code>engine/templates/index.html</code> esista.</p>
        </body>
    </html>
    """

@app.get("/api/status")
def get_status():
    unprocessed = list_unprocessed_raw()
    
    # Extract tail of log.md
    log_tail = []
    vault_path = get_vault_path()
    log_path = os.path.join(vault_path, "log.md")
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                log_tail = [l.strip() for l in lines[-15:] if l.strip()]
        except Exception:
            pass
            
    return {
        "running": manager.is_running(),
        "active_source": manager.active_source,
        "queue_count": len(unprocessed),
        "queue_preview": unprocessed[:10],
        "log_history": manager.log_history,
        "log_tail": log_tail,
        "schedule_time": get_schedule_time()
    }

@app.post("/api/ingest/start")
async def start_ingest(source: Optional[str] = None):
    started = await manager.start(source=source)
    if started:
        return {"status": "started", "source": source or "all"}
    return JSONResponse(status_code=400, content={"status": "already_running", "active_source": manager.active_source})

@app.post("/api/ingest/stop")
async def stop_ingest():
    stopped = await manager.stop()
    if stopped:
        return {"status": "stopped"}
    return JSONResponse(status_code=400, content={"status": "not_running"})

@app.post("/api/schedule")
def update_schedule(req: TimeScheduleRequest):
    success = set_schedule_time(req.time)
    if success:
        return {"status": "updated", "time": req.time}
    return JSONResponse(status_code=500, content={"status": "error_updating"})

def get_local_tasks() -> list:
    vault_path = get_vault_path()
    tasks = []
    entities_dir = os.path.join(vault_path, "wiki", "entities")
    if not os.path.exists(entities_dir):
        return tasks
    for root, _, files in os.walk(entities_dir):
        for file in files:
            if file.endswith(".md") and not file.startswith("."):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    fm, body = parse_markdown(content)
                    if fm.get("type") in ["microtheme", "task"]:
                        tasks.append({
                            "title": fm.get("title") or os.path.splitext(file)[0],
                            "status": fm.get("status") or "To Do",
                            "due_date": fm.get("due_date"),
                            "notion_page_id": fm.get("notion_page_id"),
                            "source": fm.get("source", "local"),
                            "category": os.path.basename(root),
                            "path": os.path.relpath(filepath, vault_path)
                        })
                except Exception:
                    pass
    return tasks

def get_local_meetings() -> list:
    vault_path = get_vault_path()
    meetings = []
    meetings_dirs = [
        os.path.join(vault_path, "wiki", "sources", "Riunioni"),
        os.path.join(vault_path, "Meetings")
    ]
    for m_dir in meetings_dirs:
        if not os.path.exists(m_dir):
            continue
        for root, _, files in os.walk(m_dir):
            for file in files:
                if file.endswith(".md") and not file.startswith("."):
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            content = f.read()
                        fm, body = parse_markdown(content)
                        if fm.get("type") in ["meeting", "calendar_event"]:
                            meetings.append({
                                "title": fm.get("title") or os.path.splitext(file)[0],
                                "quando": fm.get("quando") or fm.get("start_time", "")[:10],
                                "start_time": fm.get("start_time"),
                                "end_time": fm.get("end_time"),
                                "location": fm.get("location"),
                                "notion_page_id": fm.get("notion_page_id"),
                                "source": fm.get("source", "local"),
                                "path": os.path.relpath(filepath, vault_path)
                            })
                    except Exception:
                        pass
    meetings.sort(key=lambda x: x["quando"], reverse=True)
    return meetings

@app.get("/api/notion/tasks")
def api_get_tasks():
    try:
        tasks = get_local_tasks()
        return {"tasks": tasks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/notion/tasks/create")
def api_create_task(req: TaskCreateRequest):
    try:
        msg = create_notion_task(
            title=req.title,
            due_date=req.due_date,
            status=req.status,
            category=req.category
        )
        return {"status": "success", "message": msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/notion/calendar")
def api_get_calendar():
    try:
        meetings = get_local_meetings()
        return {"events": meetings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/notion/calendar/create")
def api_create_calendar_event(req: CalendarEventCreateRequest):
    try:
        msg = create_notion_calendar_event(
            title=req.title,
            start_time=req.start_time,
            end_time=req.end_time,
            location=req.location
        )
        return {"status": "success", "message": msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/prompt")
def api_get_prompt():
    vault_path = get_vault_path()
    prompt_path = os.path.join(vault_path, "agents.md")
    if not os.path.exists(prompt_path):
        raise HTTPException(status_code=404, detail="File agents.md non trovato.")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/prompt")
def api_save_prompt(req: FileEditRequest):
    vault_path = get_vault_path()
    prompt_path = os.path.join(vault_path, "agents.md")
    try:
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        # Git auto-commit
        from engine.git_ops import auto_commit
        auto_commit(vault_path, "[Dashboard Config] Aggiornato agents.md")
        return {"status": "success", "message": "agents.md salvato con successo."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/user_profile")
def api_get_user_profile():
    vault_path = get_vault_path()
    profile_path = os.path.join(vault_path, "user_profile.md")
    if not os.path.exists(profile_path):
        raise HTTPException(status_code=404, detail="File user_profile.md non trovato.")
    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/user_profile")
def api_save_user_profile(req: FileEditRequest):
    vault_path = get_vault_path()
    profile_path = os.path.join(vault_path, "user_profile.md")
    try:
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        # Git auto-commit
        from engine.git_ops import auto_commit
        auto_commit(vault_path, "[Dashboard Config] Aggiornato user_profile.md")
        return {"status": "success", "message": "user_profile.md salvato con successo."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs/stream")
async def logs_stream(request: Request):
    q = manager.register_listener()
    
    async def event_generator():
        try:
            # Send current history first
            for line in manager.log_history:
                yield f"data: {line}\n\n"
                
            # Stream new lines
            while True:
                if request.is_disconnected():
                    break
                try:
                    line = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield f"data: {line}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive heartbeat
                    yield "data: :heartbeat\n\n"
        finally:
            manager.unregister_listener(q)
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/webhook/{source}")
async def trigger_webhook(source: str, x_webhook_secret: Optional[str] = Header(None)):
    secret = os.getenv("WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="WEBHOOK_SECRET non configurato nel file .env.")
    if x_webhook_secret != secret:
        raise HTTPException(status_code=401, detail="Secret non valido o mancante.")
    
    valid_sources = ["notion", "drive", "mail", "web", "calendar", "all"]
    if source not in valid_sources:
        raise HTTPException(status_code=400, detail=f"Sorgente non valida. Deve essere una tra: {valid_sources}")
        
    started = await manager.start(source=source)
    if started:
        return {"status": "triggered", "source": source}
    return JSONResponse(status_code=400, content={"status": "already_running", "active_source": manager.active_source})

# --- MCP Server Integration ---
if mcp_server is not None:
    @mcp_server.tool()
    async def query_second_brain(question: str) -> str:
        """Interroga il secondo cervello con una domanda in linguaggio naturale e ottieni una risposta basata sui dati del vault."""
        try:
            ans = await query_agent_answer(question)
            return ans
        except Exception as e:
            return f"Errore durante l'interrogazione: {e}"

    @mcp_server.tool()
    def search_vault(query: str) -> str:
        """Cerca riferimenti e note nel vault Obsidian contenenti le parole chiave."""
        try:
            results = search_wiki(query)
            if not results:
                return "Nessun risultato trovato nel vault."
            out = []
            for r in results:
                out.append(f"- **[[{r['path'].replace('.md', '')}]]** ({r['title']}):\n  {r['snippet']}")
            return "\n\n".join(out)
        except Exception as e:
            return f"Errore durante la ricerca: {e}"

    @mcp_server.tool()
    async def trigger_ingestion_sync() -> str:
        """Avvia il processo di sincronizzazione ed ingestion delle fonti nel Secondo Cervello in background."""
        started = await manager.start()
        if started:
            return "Ingestione avviata con successo in background."
        return "L'ingestione è già in esecuzione."
        
    @mcp_server.tool()
    def get_queue_status() -> str:
        """Ottiene il numero di file in coda ed elenca i primi 10 file non ancora elaborati."""
        unprocessed = list_unprocessed_raw()
        status_running = "in esecuzione" if manager.is_running() else "fermo"
        out = [f"Stato Ingestione: {status_running}"]
        out.append(f"File in coda: {len(unprocessed)}")
        if unprocessed:
            out.append("\nPrimi file in coda:")
            for u in unprocessed[:10]:
                out.append(f"- {u}")
        return "\n".join(out)

    # Mount the MCP server's SSE application to FastAPI app at /mcp
    app.mount("/mcp", mcp_server.sse_app())

# --- Admin Dashboard & API Endpoints ---

def parse_agents_md(content: str) -> dict:
    sections = {}
    pattern = r"##\s+([^\n]+)\n(.*?)(?=\n##\s+|$)"
    matches = re.findall(pattern, content, re.DOTALL)
    for title, text in matches:
        clean_text = text.strip()
        if clean_text.endswith("---"):
            clean_text = clean_text[:-3].strip()
        sections[title.strip()] = clean_text
    return sections

def rebuild_agents_md(sections_data: dict, original_content: str) -> str:
    intro_match = re.match(r"^(.*?)(?=\n##\s+|$)", original_content, re.DOTALL)
    intro = intro_match.group(1).strip() if intro_match else ""
    rebuilt = intro + "\n\n"
    order = [
        "Identity (Linee Guida Generali)",
        "Ingest Agent",
        "Query Agent",
        "Reflect Agent",
        "Lint Agent",
        "Ontology Agent"
    ]
    for sec in order:
        if sec in sections_data:
            rebuilt += f"---\n\n## {sec}\n\n{sections_data[sec].strip()}\n\n"
    # Appende agenti custom aggiuntivi
    for sec in sections_data:
        if sec not in order:
            rebuilt += f"---\n\n## {sec}\n\n{sections_data[sec].strip()}\n\n"
    return rebuilt.strip() + "\n"

@app.get("/admin", response_class=HTMLResponse)
def read_admin():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "admin.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    return "Template admin.html non trovato."

@app.get("/api/admin/prompts")
def api_get_admin_prompts():
    vault_path = get_vault_path()
    prompt_path = os.path.join(vault_path, "agents.md")
    if not os.path.exists(prompt_path):
        raise HTTPException(status_code=404, detail="File agents.md non trovato.")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()
        prompts = parse_agents_md(content)
        return {"prompts": prompts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/prompts")
def api_save_admin_prompts(req: PromptsEditRequest):
    vault_path = get_vault_path()
    prompt_path = os.path.join(vault_path, "agents.md")
    if not os.path.exists(prompt_path):
        raise HTTPException(status_code=404, detail="File agents.md non trovato.")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            original_content = f.read()
        
        new_content = rebuild_agents_md(req.prompts, original_content)
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        from engine.git_ops import auto_commit
        auto_commit(vault_path, "[Dashboard Config] Aggiornati prompt agenti singolarmente")
        return {"status": "success", "message": "agents.md aggiornato con successo."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/ontology/history")
def api_get_ontology_history():
    vault_path = get_vault_path()
    history_path = os.path.join(vault_path, "engine", "ontology_history.json")
    if not os.path.exists(history_path):
        return {"history": []}
    try:
        with open(history_path, "r", encoding="utf-8") as f:
            history = json.load(f)
        return {"history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/ontology/run")
async def api_run_ontology():
    asyncio.create_task(run_admin_task_subprocess("Ontology Agent", ["ontology"]))
    return {"status": "started", "message": "Ontology Agent avviato."}

@app.post("/api/admin/ontology/proposals/{proposal_id}/rollback")
def api_rollback_ontology(proposal_id: str):
    success = rollback_ontology_proposal(proposal_id)
    if success:
        return {"status": "success", "message": f"Rollback della proposta {proposal_id} completato."}
    raise HTTPException(status_code=400, detail=f"Impossibile completare il rollback per la proposta {proposal_id}.")

@app.post("/api/admin/ontology/proposals/{proposal_id}/confirm")
def api_confirm_ontology(proposal_id: str):
    success = confirm_ontology_proposal(proposal_id)
    if success:
        return {"status": "success", "message": f"Proposta {proposal_id} confermata con successo."}
    raise HTTPException(status_code=400, detail=f"Impossibile confermare la proposta {proposal_id}.")

@app.get("/api/admin/settings")
def api_get_admin_settings():
    vault_path = get_vault_path()
    settings_path = os.path.join(vault_path, "settings.md")
    if not os.path.exists(settings_path):
        raise HTTPException(status_code=404, detail="settings.md non trovato.")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            content = f.read()
        fm, body = parse_markdown(content)
        
        env_vars = {}
        env_path = os.path.join(vault_path, ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip() and not line.strip().startswith("#") and "=" in line:
                        k, v = line.strip().split("=", 1)
                        env_vars[k.strip()] = v.strip().strip('"').strip("'")
                        
        return {"settings": fm, "env": env_vars}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/settings")
def api_save_admin_settings(req: SettingsEditRequest):
    vault_path = get_vault_path()
    settings_path = os.path.join(vault_path, "settings.md")
    try:
        if os.path.exists(settings_path):
            with open(settings_path, "r", encoding="utf-8") as f:
                content = f.read()
            _, body = parse_markdown(content)
        else:
            body = "# Configurazione del Secondo Cervello\n"
            
        new_content = to_markdown(req.settings, body)
        with open(settings_path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        if req.env is not None:
            env_path = os.path.join(vault_path, ".env")
            env_lines = []
            for k, v in req.env.items():
                env_lines.append(f'{k}="{v}"\n')
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(env_lines)
                
        from engine.git_ops import auto_commit
        auto_commit(vault_path, "[Dashboard Config] Aggiornate impostazioni settings.md e .env")
        return {"status": "success", "message": "Impostazioni salvate correttamente."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/automations")
def api_get_automations():
    sync_scheduled_jobs_to_db()
    
    automations = {}
    with db_session() as session:
        jobs = session.query(ScheduledJob).all()
        for job in jobs:
            key = job.target_action
            if key == "ingest":
                key = "sync_and_ingest"
            elif key == "reflect":
                key = "weekly_reflection"
            elif key == "graphify":
                key = "graphify_update"
                
            schedule_str = job.schedule_value
            if job.schedule_type == "interval" and schedule_str.isdigit():
                # Convert back to seconds for dashboard backward compatibility
                schedule_str = str(int(schedule_str) * 60)
                
            last_run_str = "Mai"
            if job.last_run:
                try:
                    last_run_str = datetime.datetime.fromtimestamp(job.last_run).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
                    
            automations[key] = {
                "id": job.id,
                "name": job.name,
                "timing": schedule_str,
                "enabled": bool(job.is_active),
                "description": f"Azione: {job.target_action}. Ultimo avvio: {last_run_str}",
                "is_custom": job.target_action not in ("ingest", "reflect", "briefing", "dream", "graphify"),
                "target_action": job.target_action,
                "schedule_type": job.schedule_type,
                "schedule_value": job.schedule_value
            }
    return {"automations": automations}

@app.post("/api/admin/automations")
def api_create_custom_automation(req: CustomAutomationCreateRequest):
    vault_path = get_vault_path()
    try:
        # 1. Salva in SQLite
        with db_session() as session:
            # Crea o aggiorna l'agente se viene fornito un prompt
            if req.agent_prompt and req.agent_prompt.strip():
                config = session.query(ProceduralConfig).filter(ProceduralConfig.agent_name == req.name).first()
                if not config:
                    config = ProceduralConfig(agent_name=req.name)
                    session.add(config)
                config.system_instructions = req.agent_prompt.strip()
                config.primary_model = "z_ai/glm-5.2"
                config.set_fallback_list(["google/gemini-3.5-pro", "openai/gpt-5"])
                
            # Crea o aggiorna il job pianificato
            job = session.query(ScheduledJob).filter(ScheduledJob.name == req.name).first()
            if not job:
                job = ScheduledJob(name=req.name)
                session.add(job)
                
            job.schedule_type = "time" if ":" in req.timing else "interval"
            timing_val = req.timing.strip()
            if timing_val.isdigit():
                val = int(timing_val)
                # Converte i secondi inseriti dalla vecchia UI in minuti per il DB
                if val >= 60:
                    job.schedule_value = str(val // 60)
                else:
                    job.schedule_value = str(val)
            else:
                job.schedule_value = timing_val
                
            job.target_action = " ".join(req.command_args)
            job.is_active = 1
            session.commit()
            
        # 2. Scrive files per retrocompatibilità come sorgente di verità secondaria
        prompt_path = os.path.join(vault_path, "agents.md")
        if req.agent_prompt and req.agent_prompt.strip() and os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                agents_content = f.read()
            sections = parse_agents_md(agents_content)
            sections[req.name] = req.agent_prompt.strip()
            new_agents_content = rebuild_agents_md(sections, agents_content)
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(new_agents_content)
                
        sync_db_to_settings_md()
        
        from engine.git_ops import auto_commit
        auto_commit(vault_path, f"[Dashboard Config] Creata nuova automazione custom in DB: {req.name}")
        
        return {"status": "success", "message": f"Automazione '{req.name}' creata con successo."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/automations/{name}/toggle")
def api_toggle_automation(name: str):
    target_action = name
    if name == "sync_and_ingest":
        target_action = "ingest"
    elif name == "weekly_reflection":
        target_action = "reflect"
    elif name == "graphify_update":
        target_action = "graphify"
        
    with db_session() as session:
        if name.isdigit():
            job = session.query(ScheduledJob).filter(ScheduledJob.id == int(name)).first()
        else:
            job = session.query(ScheduledJob).filter(
                (ScheduledJob.target_action == target_action) | (ScheduledJob.name == name)
            ).first()
            
        if not job:
            raise HTTPException(status_code=404, detail=f"Automazione {name} non trovata.")
            
        job.is_active = 0 if job.is_active == 1 else 1
        status = "enabled" if job.is_active == 1 else "disabled"
        session.commit()
        
    try:
        sync_db_to_settings_md()
    except Exception as e:
        print(f"[Sync settings.md] Errore in toggle: {e}")
        
    vault_path = get_vault_path()
    from engine.git_ops import auto_commit
    auto_commit(vault_path, f"[Dashboard Config] {'Abilitata' if status == 'enabled' else 'Disabilitata'} automazione {name}")
    return {"status": "success", "action": status, "value": job.schedule_value}

@app.post("/api/admin/automations/{name}/schedule")
def api_set_automation_schedule(name: str, req: AutomationScheduleRequest):
    target_action = name
    if name == "sync_and_ingest":
        target_action = "ingest"
    elif name == "weekly_reflection":
        target_action = "reflect"
    elif name == "graphify_update":
        target_action = "graphify"
        
    schedule_input = req.schedule.strip()
    schedule_type = "time" if ":" in schedule_input else "interval"
    
    if schedule_input.isdigit():
        val = int(schedule_input)
        if val >= 60:
            schedule_value = str(val // 60)
        else:
            schedule_value = str(val)
    else:
        schedule_value = schedule_input
        
    with db_session() as session:
        if name.isdigit():
            job = session.query(ScheduledJob).filter(ScheduledJob.id == int(name)).first()
        else:
            job = session.query(ScheduledJob).filter(
                (ScheduledJob.target_action == target_action) | (ScheduledJob.name == name)
            ).first()
            
        if not job:
            raise HTTPException(status_code=404, detail=f"Automazione {name} non trovata.")
            
        job.schedule_type = schedule_type
        job.schedule_value = schedule_value
        session.commit()
        
    try:
        sync_db_to_settings_md()
    except Exception as e:
        print(f"[Sync settings.md] Errore in schedule: {e}")
        
    vault_path = get_vault_path()
    from engine.git_ops import auto_commit
    auto_commit(vault_path, f"[Dashboard Config] Modificata pianificazione per {name} a '{schedule_input}'")
    return {"status": "success", "value": schedule_input}

@app.post("/api/admin/automations/{name}/run")
async def api_run_automation(name: str):
    target_action = name
    if name == "sync_and_ingest":
        target_action = "ingest"
    elif name == "weekly_reflection":
        target_action = "reflect"
    elif name == "graphify_update":
        target_action = "graphify"
        
    with db_session() as session:
        if name.isdigit():
            job = session.query(ScheduledJob).filter(ScheduledJob.id == int(name)).first()
        else:
            job = session.query(ScheduledJob).filter(
                (ScheduledJob.target_action == target_action) | (ScheduledJob.name == name)
            ).first()
            
        if not job:
            raise HTTPException(status_code=404, detail=f"Automazione {name} non trovata.")
            
        pretty_name = job.name
        action = job.target_action
        
    asyncio.create_task(run_task_subprocess_by_name(pretty_name, action))
    return {"status": "started", "message": f"Compito '{pretty_name}' avviato in background."}

class AgentCreateRequest(BaseModel):
    agent_name: str
    system_instructions: str
    primary_model: str
    fallback_chain: List[str] = []

@app.get("/api/admin/agents")
def api_get_agents():
    agents = []
    with db_session() as session:
        configs = session.query(ProceduralConfig).all()
        for c in configs:
            agents.append({
                "agent_name": c.agent_name,
                "system_instructions": c.system_instructions,
                "primary_model": c.primary_model,
                "fallback_chain": c.get_fallback_list()
            })
    return {"agents": agents}

@app.post("/api/admin/agents")
def api_create_agent(req: AgentCreateRequest):
    vault_path = get_vault_path()
    try:
        # 1. Scrive in SQLite
        with db_session() as session:
            config = session.query(ProceduralConfig).filter(ProceduralConfig.agent_name == req.agent_name).first()
            if not config:
                config = ProceduralConfig(agent_name=req.agent_name)
                session.add(config)
            config.system_instructions = req.system_instructions.strip()
            config.primary_model = req.primary_model
            config.set_fallback_list(req.fallback_chain)
            session.commit()
            
        # 2. Sincronizza agents.md
        prompt_path = os.path.join(vault_path, "agents.md")
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                agents_content = f.read()
            sections = parse_agents_md(agents_content)
            sections[req.agent_name] = req.system_instructions.strip()
            new_agents_content = rebuild_agents_md(sections, agents_content)
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(new_agents_content)
                
        # 3. Sincronizza settings.md
        settings_path = os.path.join(vault_path, "settings.md")
        if os.path.exists(settings_path):
            with open(settings_path, "r", encoding="utf-8") as f:
                settings_content = f.read()
            fm, body = parse_markdown(settings_content)
            if "models" not in fm:
                fm["models"] = {}
            fm["models"][req.agent_name] = {
                "primary": req.primary_model,
                "fallback": req.fallback_chain
            }
            from engine.utils.markdown import to_markdown
            new_settings_content = to_markdown(fm, body)
            with open(settings_path, "w", encoding="utf-8") as f:
                f.write(new_settings_content)
                
        from engine.git_ops import auto_commit
        auto_commit(vault_path, f"[Dashboard Config] Creato/aggiornato agente {req.agent_name}")
        return {"status": "success", "message": f"Agente '{req.agent_name}' creato o aggiornato con successo."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def run_admin_task_subprocess(name: str, command_args: list[str]):
    vault_path = get_vault_path()
    python_exe = os.path.join(vault_path, ".venv", "bin", "python")
    if not os.path.exists(python_exe):
        python_exe = sys.executable
        
    args = [python_exe, "-u", "-m", "engine.main"] + command_args
    manager.write_log(f"[ADMIN] Avvio compito: {name}...")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=vault_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="ignore").strip()
            manager.write_log(f"[{name}] {line}")
            
        await proc.wait()
        manager.write_log(f"[ADMIN] Compito completato: {name} con codice {proc.returncode}")
    except Exception as e:
        manager.write_log(f"[ADMIN] Errore nell'esecuzione di {name}: {e}")

    # Mount the MCP server's SSE application to FastAPI app at /mcp
    app.mount("/mcp", mcp_server.sse_app())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
