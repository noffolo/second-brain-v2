import os
import json
import ssl
import urllib.request
import urllib.error
import asyncio
from google.antigravity import Agent, LocalAgentConfig
from google.antigravity.types import CapabilitiesConfig

try:
    ssl_context = ssl._create_unverified_context()
except AttributeError:
    ssl_context = None

_original_gemini_key_pool = None
_free4all_cookie = None

def resolve_gemini_key(model: str = None) -> str:
    """
    Risolve e ruota le chiavi API di Gemini da una lista separata da virgole in GEMINI_API_KEY.
    Memorizza la lista originale, filtra per chiavi non soggette a rate limit per il modello specificato,
    e seleziona a caso una delle chiavi sane, impostandola in os.environ["GEMINI_API_KEY"].
    """
    global _original_gemini_key_pool
    import random
    
    if _original_gemini_key_pool is None:
        _original_gemini_key_pool = os.getenv("GEMINI_API_KEY", "").strip()
        
    if not _original_gemini_key_pool:
        return ""
        
    if "," not in _original_gemini_key_pool:
        return _original_gemini_key_pool
        
    keys = [k.strip() for k in _original_gemini_key_pool.split(",") if k.strip()]
    if not keys:
        return ""
        
    # Filtra le chiavi che non sono in rate limit per il modello fornito
    available_keys = [k for k in keys if not is_key_rate_limited(k, model)]
    if not available_keys:
        # Se tutte le chiavi sono limitate, usa l'intero pool come ultima risorsa
        available_keys = keys
        
    selected_key = random.choice(available_keys)
    os.environ["GEMINI_API_KEY"] = selected_key
    return selected_key

def get_gemini_keys() -> list:
    """
    Ritorna la lista di tutte le chiavi Gemini configurate.
    """
    global _original_gemini_key_pool
    if _original_gemini_key_pool is None:
        _original_gemini_key_pool = os.getenv("GEMINI_API_KEY", "").strip()
        
    if not _original_gemini_key_pool:
        return []
        
    if "," not in _original_gemini_key_pool:
        return [_original_gemini_key_pool]
        
    return [k.strip() for k in _original_gemini_key_pool.split(",") if k.strip()]

# Circuit Breaker per i modelli che hanno esaurito la quota
RATE_LIMITS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".rate_limits.json")

def load_rate_limited_models() -> set:
    import time
    if not os.path.exists(RATE_LIMITS_FILE):
        return set()
    try:
        with open(RATE_LIMITS_FILE, "r") as f:
            data = json.load(f)
        now = time.time()
        active = set()
        for model, expiry in data.items():
            if now < expiry:
                active.add(model)
        return active
    except Exception as e:
        print(f"[Circuit Breaker] Errore di lettura file limitazioni: {e}")
        return set()

def save_rate_limited_model(model: str, duration_seconds: int = 1800):
    import time
    data = {}
    if os.path.exists(RATE_LIMITS_FILE):
        try:
            with open(RATE_LIMITS_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            pass
    
    now = time.time()
    clean_data = {}
    for m, expiry in data.items():
        if now < expiry:
            clean_data[m] = expiry
            
    clean_data[model] = now + duration_seconds
    
    try:
        with open(RATE_LIMITS_FILE, "w") as f:
            json.dump(clean_data, f)
        print(f"[Circuit Breaker] Modello {model} registrato come limitato fino a {time.strftime('%H:%M:%S', time.localtime(now + duration_seconds))}")
    except Exception as e:
        print(f"[Circuit Breaker] Errore di scrittura file limitazioni: {e}")

def is_key_rate_limited(api_key: str, model: str = None) -> bool:
    import time
    if not os.path.exists(RATE_LIMITS_FILE):
        return False
    try:
        with open(RATE_LIMITS_FILE, "r") as f:
            data = json.load(f)
        now = time.time()
        key_id = f"key_{api_key[:12]}"
        if model:
            key_id = f"key_{api_key[:12]}_{model}"
        expiry = data.get(key_id)
        if expiry and now < expiry:
            return True
    except Exception:
        pass
    return False

def save_rate_limited_key(api_key: str, model: str = None, duration_seconds: int = 300):
    import time
    data = {}
    if os.path.exists(RATE_LIMITS_FILE):
        try:
            with open(RATE_LIMITS_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            pass
    
    now = time.time()
    key_id = f"key_{api_key[:12]}"
    if model:
        key_id = f"key_{api_key[:12]}_{model}"
    data[key_id] = now + duration_seconds
    
    # Pulisci record scaduti
    clean_data = {}
    for k, expiry in data.items():
        if now < expiry:
            clean_data[k] = expiry
            
    try:
        with open(RATE_LIMITS_FILE, "w") as f:
            json.dump(clean_data, f)
        model_str = f" per modello {model}" if model else ""
        print(f"[Circuit Breaker] Chiave {api_key[:10]}...{model_str} registrata come limitata per {duration_seconds}s.", flush=True)
    except Exception as e:
        print(f"[Circuit Breaker] Errore di scrittura file limitazioni: {e}", flush=True)

# ----------------- RECUPERO CHIAVI DA FREE4ALL -----------------

async def fetch_keys_from_free4all(provider_name: str) -> list[str]:
    """
    Autentica sul portale Free4All ed estrae le chiavi attive per il provider specificato.
    """
    global _free4all_cookie
    import urllib.request
    import urllib.parse
    import json
    import ssl
    import os
    
    password = os.getenv("FREE4ALL_PASSWORD", "W3ar3pirat3s!2026")
    if not password:
        print("[Free4All] Nessuna password impostata per Free4All API.")
        return []
        
    user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    # 1. Effettua login se non abbiamo ancora il cookie
    if not _free4all_cookie:
        login_url = "https://free4all.lascuolaopensource.xyz/login"
        login_data = urllib.parse.urlencode({"password": password}).encode("utf-8")
        req = urllib.request.Request(login_url, data=login_data, method="POST")
        req.add_header("User-Agent", user_agent)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        
        loop = asyncio.get_running_loop()
        try:
            def do_login():
                class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        return None
                opener = urllib.request.build_opener(
                    NoRedirectHandler, 
                    urllib.request.HTTPSHandler(context=ssl_context)
                )
                try:
                    resp = opener.open(req)
                except urllib.error.HTTPError as e:
                    resp = e
                return resp.info().get("Set-Cookie")
                
            cookie_hdr = await loop.run_in_executor(None, do_login)
            if cookie_hdr:
                cookies = [c.strip() for c in cookie_hdr.split(";")]
                for c in cookies:
                    if c.startswith("dashboard_auth="):
                        _free4all_cookie = c
                        print("[Free4All] Autenticato con successo. Sessione cookie salvata.")
                        break
        except Exception as e:
            print(f"[Free4All] Errore durante il login: {e}")
            return []
            
    if not _free4all_cookie:
        print("[Free4All] Impossibile autenticare o ottenere sessione.")
        return []
        
    # 2. Richiedi le chiavi
    api_url = "https://free4all.lascuolaopensource.xyz/keys/api"
    req = urllib.request.Request(api_url)
    req.add_header("User-Agent", user_agent)
    req.add_header("Cookie", _free4all_cookie)
    
    loop = asyncio.get_running_loop()
    try:
        def do_get_keys():
            with urllib.request.urlopen(req, context=ssl_context, timeout=10) as resp:
                return resp.read().decode("utf-8")
                
        resp_body = await loop.run_in_executor(None, do_get_keys)
        resp_json = json.loads(resp_body)
        
        keys_list = []
        for k in resp_json.get("data", []):
            if k.get("is_active") is True:
                if k.get("provider", "").lower() == provider_name.lower():
                    key_val = k.get("key")
                    if key_val:
                        keys_list.append(key_val)
                        
        print(f"[Free4All] Trovate {len(keys_list)} chiavi attive per il provider {provider_name}.")
        return keys_list
    except Exception as e:
        print(f"[Free4All] Errore di recupero chiavi: {e}")
        _free4all_cookie = None
        return []

def save_key_to_env(provider_name: str, key_val: str):
    """
    Persiste la chiave funzionante nel file .env locale accodandola a quelle esistenti.
    """
    from engine.tools.vault_tools import get_vault_path
    vault_path = get_vault_path()
    env_path = os.path.join(vault_path, ".env")
    if not os.path.exists(env_path):
        return
        
    env_var_map = {
        "google": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "together": "TOGETHER_API_KEY",
        "dashscope": "DASHSCOPE_API_KEY",
        "baidu": "BAIDU_API_KEY",
        "stability": "STABILITY_API_KEY",
        "elevenlabs": "ELEVENLABS_API_KEY",
        "deepgram": "DEEPGRAM_API_KEY"
    }
    
    var_name = env_var_map.get(provider_name.lower())
    if not var_name:
        return
        
    try:
        lines = []
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        updated = False
        new_lines = []
        for line in lines:
            if line.strip().startswith(f"{var_name}="):
                parts = line.strip().split("=", 1)
                curr_val = parts[1].strip().strip('"').strip("'")
                
                if key_val in curr_val:
                    new_lines.append(line)
                    updated = True
                    continue
                    
                if var_name == "GEMINI_API_KEY" and curr_val:
                    new_val = f"{key_val},{curr_val}"
                else:
                    new_val = key_val
                    
                new_lines.append(f'{var_name}="{new_val}"\n')
                updated = True
            else:
                new_lines.append(line)
                
        if not updated:
            new_lines.append(f'\n{var_name}="{key_val}"\n')
            
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            
        os.environ[var_name] = key_val
        print(f"[Free4All] Salvata persistenza in .env per {var_name}.")
    except Exception as e:
        print(f"[Free4All] Errore di scrittura in .env: {e}")

# ----------------- CHIAMATE API GENERALI -----------------

def parse_provider_model(model_str: str) -> tuple[str, str]:
    if not model_str:
        return None, None
    if "/" in model_str:
        prov, model = model_str.split("/", 1)
        return prov.strip().lower(), model.strip()
    
    model_lower = model_str.lower()
    if model_lower.startswith("gemini"):
        return "google", model_str
    elif model_lower.startswith("gpt") or model_lower.startswith("o1") or model_lower.startswith("o3") or model_lower.startswith("o4"):
        return "openai", model_str
    elif model_lower.startswith("deepseek"):
        return "deepseek", model_str
    elif model_lower.startswith("qwen"):
        return "dashscope", model_str
    elif model_lower.startswith("ernie"):
        return "baidu", model_str
    elif model_lower.startswith("granite") or model_lower.startswith("llama"):
        return "ollama", model_str
    elif model_lower.startswith("glm"):
        return "z_ai", model_str
        
    return "google", model_str

async def call_openai_compatible_api(url: str, api_key: str, model: str, system_instructions: str, prompt: str, timeout: int = 90) -> str:
    """
    Effettua una chiamata HTTP asincrona a un endpoint compatibile con OpenAI.
    """
    if system_instructions and not isinstance(system_instructions, str):
        system_instructions = getattr(system_instructions, "identity", getattr(system_instructions, "text", str(system_instructions)))
        
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": prompt}
        ]
    }
    
    is_reasoning_only = "reasoner" in model or "o1" in model or "o3" in model
    if "json" in prompt.lower() and not is_reasoning_only:
        payload["response_format"] = {"type": "json_object"}

    data = json.dumps(payload).encode("utf-8")
    
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    loop = asyncio.get_running_loop()
    max_retries = 3
    base_delay = 4
    for attempt in range(max_retries + 1):
        try:
            def do_request():
                with urllib.request.urlopen(req, context=ssl_context, timeout=timeout) as response:
                    return response.read().decode("utf-8")
                    
            resp_body = await loop.run_in_executor(None, do_request)
            resp_json = json.loads(resp_body)
            return resp_json["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err_content = e.read().decode("utf-8") if e.fp else str(e)
            is_hard_quota = any(x in err_content.lower() for x in ["quota", "insufficient balance", "billing details", "exceeded your current quota", "insufficient_quota"])
            if is_hard_quota:
                raise RuntimeError(f"HTTP Error {e.code} (Quota Exceeded): {err_content}")
                
            is_rate_limit = (e.code == 429) or any(x in err_content.lower() for x in ["rate limit", "too many requests"])
            
            if e.code == 400 and "response_format" in payload:
                del payload["response_format"]
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("Authorization", f"Bearer {api_key}")
                req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
                continue
                
            if is_rate_limit and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                print(f"HTTP Error {e.code} (Rate Limit) da {url}. Attesa di {delay}s...")
                await asyncio.sleep(delay)
            else:
                raise RuntimeError(f"HTTP Error {e.code}: {err_content}")
        except Exception as e:
            err_str = str(e).lower()
            is_hard_quota = any(x in err_str for x in ["quota", "insufficient balance", "billing details", "exceeded your current quota", "insufficient_quota"])
            if is_hard_quota:
                raise RuntimeError(f"Error (Quota Exceeded): {e}")
                
            is_rate_limit = any(x in err_str for x in ["429", "rate limit", "too many requests"])
            if is_rate_limit and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                raise RuntimeError(f"{e}")

async def call_native_gemini_api(
    model: str, 
    api_key: str, 
    system_instructions: str, 
    prompt: str, 
    timeout: int = 90,
    use_vertex: bool = False,
    project: str = None,
    location: str = None
) -> str:
    """
    Invia una richiesta alle API di Google Gemini (via API Key o Vertex AI) usando l'SDK google-genai.
    """
    import asyncio
    from google.genai import Client, types
    from google.genai.errors import APIError

    if system_instructions and not isinstance(system_instructions, str):
        system_instructions = getattr(system_instructions, "identity", getattr(system_instructions, "text", str(system_instructions)))

    if use_vertex:
        client = Client(vertexai=True, project=project, location=location)
    else:
        if not api_key:
            raise ValueError("GEMINI_API_KEY non fornita.")
        client = Client(api_key=api_key)

    config = types.GenerateContentConfig(
        system_instruction=system_instructions,
        temperature=0.2,
    )
    if "json" in prompt.lower():
        config.response_mime_type = "application/json"

    max_retries = 3
    base_delay = 4
    for attempt in range(max_retries + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config
                ),
                timeout=timeout
            )
            if response.text and response.text.strip():
                return response.text
            raise RuntimeError("Risposta vuota da Gemini API")
        except APIError as e:
            err_msg = e.message or ""
            is_rate_limit = (e.code == 429) or any(x in err_msg.lower() for x in ["quota", "rate limit", "resource_exhausted"])
            if is_rate_limit:
                raise RuntimeError(f"HTTP Error 429 (Rate Limit): {err_msg}")
            elif attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                raise RuntimeError(f"HTTP Error {e.code}: {err_msg}")
        except asyncio.TimeoutError:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                raise RuntimeError("Chiamata a Gemini scaduta (Timeout)")
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = any(x in err_str for x in ["429", "quota", "rate limit", "resource_exhausted"])
            if is_rate_limit:
                raise RuntimeError(f"HTTP Error 429 (Rate Limit): {e}")
            elif attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                raise RuntimeError(f"{e}")

def format_worst_case_fallback(prompt: str, errors: list) -> str:
    import re
    question = ""
    question_match = re.search(r"La nuova richiesta dell'utente è la seguente:\s*(.*?)(?=\n\n---|\nRisultati della ricerca|$)", prompt, re.DOTALL)
    if question_match:
        question = question_match.group(1).strip()
    else:
        lines = prompt.splitlines()
        for line in lines:
            if line.startswith("Utente:"):
                question = line.replace("Utente:", "", 1).strip()
                break
        if not question:
            question = "\n".join(lines[:3])

    context = ""
    context_match = re.search(r"Risultati della ricerca nel vault:\s*(.*)", prompt, re.DOTALL)
    if context_match:
        context = context_match.group(1).strip()
    
    stats = ""
    stats_match = re.search(r"--- Dati Statistici Aggregati Del Secondo Cervello ---\s*(.*?)(?=\nRisultati della ricerca|$)", prompt, re.DOTALL)
    if stats_match:
        stats = stats_match.group(1).strip()

    error_details = "\n".join([f"- {err}" for err in errors])
    
    response = (
        "⚠️ **[Servizio AI Temporaneamente Non Disponibile]**\n\n"
        "Gentile utente, tutti i provider di intelligenza artificiale configurati (Gemini, Vertex AI, DeepSeek, ecc.) "
        "sono attualmente congestionati, offline o hanno esaurito la quota giornaliera.\n\n"
        "Per garantirti comunque l'accesso alle tue informazioni, ti mostro di seguito i dati e i documenti "
        "estratti direttamente dal tuo Secondo Cervello (RAG locale) per la tua richiesta:\n\n"
    )
    
    if stats:
        response += "### 📊 Statistiche Rilevanti:\n"
        response += f"```\n{stats}\n```\n\n"
        
    if context:
        response += "### 📂 Documenti ed Estratti Rilevanti Trovati:\n\n"
        response += context + "\n"
    else:
        response += "*Nessun documento rilevante trovato nel vault locale per questa query.*\n\n"
        
    response += (
        "\n---\n"
        "**Dettagli degli errori di connessione riscontrati:**\n"
        f"```\n{error_details}\n```"
    )
    return response

# ----------------- MAIN FALLBACK ENGINE -----------------

async def call_llm_with_fallback(prompt: str, system_instructions: str, gemini_config: LocalAgentConfig, agent_name: str = None) -> str:
    """
    Interroga i modelli dell'agente configurati in settings.md seguendo la catena di fallback specifica.
    Se fallisce la catena configurata, interroga Free4All per chiavi attive e le testa in tempo reale,
    salvandole in caso di successo.
    """
    
    # 1. Carica le impostazioni di configurazione
    try:
        from engine.utils.markdown import load_settings
        from engine.tools.vault_tools import get_vault_path
        settings = load_settings(get_vault_path())
    except Exception as e:
        print(f"[Fallback Settings] Fallito caricamento impostazioni: {e}")
        settings = {}

    # 2. Risoluzione della catena di tentativi dell'agente
    agent_key = agent_name or "query_agent"
    agent_cfg = settings.get("models", {}).get(agent_key)
    
    attempts = []
    if isinstance(agent_cfg, dict):
        primary_str = agent_cfg.get("primary", "")
        fallback_list = agent_cfg.get("fallback", [])
        
        if primary_str:
            p_prov, p_mod = parse_provider_model(primary_str)
            if p_prov:
                attempts.append({"provider": p_prov, "model": p_mod, "original": primary_str})
                
        for fb_str in fallback_list:
            f_prov, f_mod = parse_provider_model(fb_str)
            if f_prov:
                attempts.append({"provider": f_prov, "model": f_mod, "original": fb_str})
    elif isinstance(agent_cfg, str):
        p_prov, p_mod = parse_provider_model(agent_cfg)
        if p_prov:
            attempts.append({"provider": p_prov, "model": p_mod, "original": agent_cfg})
            
    # Fallback su impostazioni passate nel gemini_config se non ci sono tentativi
    if not attempts:
        p_prov, p_mod = parse_provider_model(gemini_config.model)
        if p_prov:
            attempts.append({"provider": p_prov, "model": p_mod, "original": gemini_config.model})
            
    if not attempts:
        attempts.append({"provider": "google", "model": "gemini-3.5-flash", "original": "google/gemini-3.5-flash"})
        
    errors = []
    
    # Carica impostazioni Vertex
    use_vertex = False
    vertex_project = None
    vertex_location = "us-central1"
    auth = settings.get("google_auth", {})
    if auth.get("use_vertex", False):
        use_vertex = True
        vertex_project = auth.get("project_id") or None
        vertex_location = auth.get("location", "us-central1")

    # 3. Esecuzione della catena ordinata
    for attempt in attempts:
        provider = attempt["provider"]
        model = attempt["model"]
        
        # Circuit Breaker check
        rate_limited_models = load_rate_limited_models()
        if f"{provider}/{model}" in rate_limited_models:
            errors.append(f"{provider}/{model}: Saltato (in quota-limit).")
            continue
            
        # Normalizzazione/Mapping dei modelli per i provider specifici
        actual_model = model
        if provider == "google":
            if model == "gemini-3.5-pro":
                actual_model = "gemini-2.5-pro"
            elif model == "gemini-3.5-flash":
                actual_model = "gemini-3.5-flash"
        elif provider == "openai":
            if model == "gpt-5":
                actual_model = "gpt-4o"
        elif provider == "together":
            if "max" in model.lower() or "reason" in model.lower() or "minimax" in model.lower():
                actual_model = "deepseek-ai/DeepSeek-V4-Pro"
            elif "llama" in model.lower():
                actual_model = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
        elif provider == "dashscope":
            if "max" in model.lower():
                actual_model = "qwen-max"
                
        try:
            if provider == "google":
                # Rotazione chiavi locali
                keys = get_gemini_keys()
                
                # Prova Vertex se abilitato
                if use_vertex:
                    try:
                        print(f"Tentativo Gemini ({actual_model}) via Vertex AI...")
                        resp_text = await call_native_gemini_api(
                            model=actual_model,
                            api_key=None,
                            system_instructions=system_instructions,
                            prompt=prompt,
                            timeout=90,
                            use_vertex=True,
                            project=vertex_project,
                            location=vertex_location
                        )
                        if resp_text and resp_text.strip():
                            return resp_text
                    except Exception as e:
                        errors.append(f"Vertex AI ({actual_model}): {e}")
                
                # Prova chiavi API locali
                for current_key in keys:
                    if is_key_rate_limited(current_key, actual_model):
                        continue
                    os.environ["GEMINI_API_KEY"] = current_key
                    try:
                        resp_text = await call_native_gemini_api(
                            model=actual_model,
                            api_key=current_key,
                            system_instructions=system_instructions,
                            prompt=prompt,
                            timeout=90
                        )
                        if resp_text and resp_text.strip():
                            return resp_text
                    except Exception as e:
                        errors.append(f"Gemini API ({actual_model}) chiave {current_key[:10]}: {e}")
                        err_str = str(e).lower()
                        if any(x in err_str for x in ["429", "resource_exhausted", "quota", "rate limit"]):
                            save_rate_limited_key(current_key, actual_model)
                            
                # Se falliscono le chiavi locali, prova Free4All!
                free_keys = await fetch_keys_from_free4all("google")
                for fk in free_keys:
                    try:
                        resp_text = await call_native_gemini_api(
                            model=actual_model,
                            api_key=fk,
                            system_instructions=system_instructions,
                            prompt=prompt,
                            timeout=90
                        )
                        if resp_text and resp_text.strip():
                            save_key_to_env("google", fk)
                            return resp_text
                    except Exception as e:
                        errors.append(f"Free4All Gemini ({actual_model}) chiave {fk[:10]}: {e}")
                        
            elif provider in ("z_ai", "zai"):
                keys_to_try = []
                local_key = os.getenv("Z_AI_API_KEY")
                if local_key and not local_key.startswith("YOUR_"):
                    keys_to_try.append(local_key)
                    
                free_keys = await fetch_keys_from_free4all("z_ai")
                for fk in free_keys:
                    if fk not in keys_to_try:
                        keys_to_try.append(fk)
                        
                for api_key in keys_to_try:
                    try:
                        resp_text = await call_openai_compatible_api(
                            url="https://api.z.ai/api/paas/v4/chat/completions",
                            api_key=api_key,
                            model=actual_model,
                            system_instructions=system_instructions,
                            prompt=prompt
                        )
                        if resp_text and resp_text.strip():
                            if api_key != os.getenv("Z_AI_API_KEY"):
                                save_key_to_env("z_ai", api_key)
                            return resp_text
                    except Exception as e:
                        errors.append(f"Z_AI ({actual_model}) chiave {api_key[:10]}: {e}")

            elif provider == "openai":
                keys_to_try = []
                local_key = os.getenv("OPENAI_API_KEY")
                if local_key and not local_key.startswith("YOUR_"):
                    keys_to_try.append(local_key)
                    
                free_keys = await fetch_keys_from_free4all("openai")
                for fk in free_keys:
                    if fk not in keys_to_try:
                        keys_to_try.append(fk)
                        
                for api_key in keys_to_try:
                    try:
                        resp_text = await call_openai_compatible_api(
                            url="https://api.openai.com/v1/chat/completions",
                            api_key=api_key,
                            model=actual_model,
                            system_instructions=system_instructions,
                            prompt=prompt
                        )
                        if resp_text and resp_text.strip():
                            if api_key != os.getenv("OPENAI_API_KEY"):
                                save_key_to_env("openai", api_key)
                            return resp_text
                    except Exception as e:
                        errors.append(f"OpenAI ({actual_model}) chiave {api_key[:10]}: {e}")
                        
            elif provider == "deepseek":
                keys_to_try = []
                local_key = os.getenv("DEEPSEEK_API_KEY")
                if local_key and not local_key.startswith("YOUR_"):
                    keys_to_try.append(local_key)
                    
                free_keys = await fetch_keys_from_free4all("deepseek")
                for fk in free_keys:
                    if fk not in keys_to_try:
                        keys_to_try.append(fk)
                        
                for api_key in keys_to_try:
                    try:
                        resp_text = await call_openai_compatible_api(
                            url="https://api.deepseek.com/chat/completions",
                            api_key=api_key,
                            model=actual_model,
                            system_instructions=system_instructions,
                            prompt=prompt
                        )
                        if resp_text and resp_text.strip():
                            if api_key != os.getenv("DEEPSEEK_API_KEY"):
                                save_key_to_env("deepseek", api_key)
                            return resp_text
                    except Exception as e:
                        errors.append(f"DeepSeek ({actual_model}) chiave {api_key[:10]}: {e}")
                        
            elif provider == "together":
                keys_to_try = []
                local_key = os.getenv("TOGETHER_API_KEY")
                if local_key and not local_key.startswith("YOUR_"):
                    keys_to_try.append(local_key)
                    
                free_keys = await fetch_keys_from_free4all("together")
                for fk in free_keys:
                    if fk not in keys_to_try:
                        keys_to_try.append(fk)
                        
                for api_key in keys_to_try:
                    try:
                        resp_text = await call_openai_compatible_api(
                            url="https://api.together.xyz/v1/chat/completions",
                            api_key=api_key,
                            model=actual_model,
                            system_instructions=system_instructions,
                            prompt=prompt
                        )
                        if resp_text and resp_text.strip():
                            if api_key != os.getenv("TOGETHER_API_KEY"):
                                save_key_to_env("together", api_key)
                            return resp_text
                    except Exception as e:
                        errors.append(f"Together ({actual_model}) chiave {api_key[:10]}: {e}")
                        
            elif provider == "dashscope":
                keys_to_try = []
                local_key = os.getenv("DASHSCOPE_API_KEY")
                if local_key and not local_key.startswith("YOUR_"):
                    keys_to_try.append(local_key)
                    
                free_keys = await fetch_keys_from_free4all("dashscope")
                for fk in free_keys:
                    if fk not in keys_to_try:
                        keys_to_try.append(fk)
                        
                for api_key in keys_to_try:
                    try:
                        resp_text = await call_openai_compatible_api(
                            url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                            api_key=api_key,
                            model=actual_model,
                            system_instructions=system_instructions,
                            prompt=prompt
                        )
                        if resp_text and resp_text.strip():
                            if api_key != os.getenv("DASHSCOPE_API_KEY"):
                                save_key_to_env("dashscope", api_key)
                            return resp_text
                    except Exception as e:
                        errors.append(f"DashScope ({actual_model}) chiave {api_key[:10]}: {e}")
                        
            elif provider == "baidu":
                keys_to_try = []
                local_key = os.getenv("BAIDU_API_KEY")
                if local_key and not local_key.startswith("YOUR_"):
                    keys_to_try.append(local_key)
                    
                free_keys = await fetch_keys_from_free4all("baidu")
                for fk in free_keys:
                    if fk not in keys_to_try:
                        keys_to_try.append(fk)
                        
                for api_key in keys_to_try:
                    try:
                        resp_text = await call_openai_compatible_api(
                            url="https://qianfan.baidubce.com/v2/chat/completions",
                            api_key=api_key,
                            model=actual_model,
                            system_instructions=system_instructions,
                            prompt=prompt
                        )
                        if resp_text and resp_text.strip():
                            if api_key != os.getenv("BAIDU_API_KEY"):
                                save_key_to_env("baidu", api_key)
                            return resp_text
                    except Exception as e:
                        errors.append(f"Baidu ({actual_model}) chiave {api_key[:10]}: {e}")
                        
            elif provider == "ollama":
                ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip('/')
                try:
                    resp_text = await call_openai_compatible_api(
                        url=f"{ollama_host}/v1/chat/completions",
                        api_key="ollama",
                        model=model,
                        system_instructions=system_instructions,
                        prompt=prompt,
                        timeout=90
                    )
                    if resp_text and resp_text.strip():
                        return resp_text
                except Exception as e:
                    errors.append(f"Ollama ({model}): {e}")
                    
        except Exception as e:
            errors.append(f"Errore generico {provider}/{model}: {e}")
            
        save_rate_limited_model(f"{provider}/{model}")

    error_summary = " | ".join(errors)
    print(f"[Fallback Outage] Tutti i tentativi sono falliti: {error_summary}", flush=True)
    try:
        return format_worst_case_fallback(prompt, errors)
    except Exception as format_err:
        raise RuntimeError(f"Tutti i provider di fallback sono falliti. Dettagli: {error_summary}")

# ----------------- AUDIO & SPEECH TRANSCRIPTION -----------------

async def call_gemini_transcribe_raw(model: str, key: str, audio_base64: str, mime_type: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"inlineData": {"mimeType": mime_type, "data": audio_base64}},
                    {"text": (
                        "Trascrivi fedelmente questo audio in lingua italiana. "
                        "Restituisci solo ed esclusivamente la trascrizione letterale dell'audio, "
                        "senza alcuna introduzione, commento, formattazione aggiuntiva o spiegazione."
                    )}
                ]
            }
        ]
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    
    loop = asyncio.get_running_loop()
    def do_request():
        with urllib.request.urlopen(req, context=ssl_context, timeout=60) as response:
            return response.read().decode("utf-8")
            
    resp_body = await loop.run_in_executor(None, do_request)
    resp_json = json.loads(resp_body)
    return resp_json["candidates"][0]["content"]["parts"][0]["text"].strip()

async def call_deepgram_transcribe(key: str, audio_base64: str, mime_type: str) -> str:
    import base64
    audio_bytes = base64.b64decode(audio_base64)
    url = "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true&language=it"
    req = urllib.request.Request(url, data=audio_bytes, method="POST")
    req.add_header("Authorization", f"Token {key}")
    req.add_header("Content-Type", mime_type)
    
    loop = asyncio.get_running_loop()
    def do_request():
        with urllib.request.urlopen(req, context=ssl_context, timeout=60) as response:
            return response.read().decode("utf-8")
            
    resp_body = await loop.run_in_executor(None, do_request)
    resp_json = json.loads(resp_body)
    return resp_json["results"]["channels"][0]["alternatives"][0]["transcript"].strip()

async def call_elevenlabs_transcribe(key: str, audio_base64: str, mime_type: str) -> str:
    import base64
    audio_bytes = base64.b64decode(audio_base64)
    
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    body_parts = []
    body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
    body_parts.append(f'Content-Disposition: form-data; name="file"; filename="audio.ogg"\r\nContent-Type: {mime_type}\r\n\r\n'.encode("utf-8"))
    body_parts.append(audio_bytes)
    body_parts.append(f"\r\n--{boundary}\r\n".encode("utf-8"))
    body_parts.append('Content-Disposition: form-data; name="model_id"\r\n\r\nscribe_v2\r\n'.encode("utf-8"))
    body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(body_parts)
    
    url = "https://api.elevenlabs.io/v1/speech-to-text"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("xi-api-key", key)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    
    loop = asyncio.get_running_loop()
    def do_request():
        with urllib.request.urlopen(req, context=ssl_context, timeout=60) as response:
            return response.read().decode("utf-8")
            
    resp_body = await loop.run_in_executor(None, do_request)
    resp_json = json.loads(resp_body)
    return resp_json["text"].strip()

async def transcribe_audio_with_fallback(audio_base64: str, mime_type: str = "audio/ogg") -> str:
    """
    Trascrive l'audio provando prima Gemini. Se fallisce, prova Deepgram, poi ElevenLabs.
    """
    errors = []
    
    # 1. Prova Gemini
    try:
        keys = get_gemini_keys()
        models = ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
        for model in models:
            for current_key in keys:
                try:
                    res = await call_gemini_transcribe_raw(model, current_key, audio_base64, mime_type)
                    if res:
                        return res
                except Exception as e:
                    errors.append(f"Gemini {model}: {e}")
    except Exception as e:
        errors.append(f"Inizializzazione Gemini: {e}")
        
    # 2. Prova Deepgram
    deepgram_key = os.getenv("DEEPGRAM_API_KEY")
    if not deepgram_key or deepgram_key.startswith("YOUR_"):
        dg_keys = await fetch_keys_from_free4all("deepgram")
        if dg_keys:
            deepgram_key = dg_keys[0]
            save_key_to_env("deepgram", deepgram_key)
            
    if deepgram_key:
        try:
            print("[Fallback Transcribe] Tentativo con Deepgram...")
            res = await call_deepgram_transcribe(deepgram_key, audio_base64, mime_type)
            if res:
                return res
        except Exception as e:
            errors.append(f"Deepgram: {e}")
            
    # 3. Prova ElevenLabs (Scribe)
    eleven_key = os.getenv("ELEVENLABS_API_KEY")
    if not eleven_key or eleven_key.startswith("YOUR_"):
        el_keys = await fetch_keys_from_free4all("elevenlabs")
        if el_keys:
            eleven_key = el_keys[0]
            save_key_to_env("elevenlabs", eleven_key)
            
    if eleven_key:
        try:
            print("[Fallback Transcribe] Tentativo con ElevenLabs...")
            res = await call_elevenlabs_transcribe(eleven_key, audio_base64, mime_type)
            if res:
                return res
        except Exception as e:
            errors.append(f"ElevenLabs: {e}")
            
    raise RuntimeError(f"Tutti i provider di trascrizione audio (Gemini, Deepgram, ElevenLabs) sono falliti: {', '.join(errors)}")

transcribe_audio_via_gemini = transcribe_audio_with_fallback

async def synthesize_speech_via_elevenlabs(text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM") -> bytes:
    """
    Sintetizza il testo in audio MP3 utilizzando ElevenLabs.
    """
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key or key.startswith("YOUR_"):
        keys = await fetch_keys_from_free4all("elevenlabs")
        if keys:
            key = keys[0]
            save_key_to_env("elevenlabs", key)
            
    if not key:
        raise ValueError("ELEVENLABS_API_KEY non configurata.")
        
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("xi-api-key", key)
    req.add_header("Content-Type", "application/json")
    
    loop = asyncio.get_running_loop()
    def do_request():
        with urllib.request.urlopen(req, context=ssl_context, timeout=60) as response:
            return response.read()
            
    return await loop.run_in_executor(None, do_request)
