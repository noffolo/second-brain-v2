import os
import shutil
import pytest
import asyncio
from engine.tools.vault_tools import get_vault_path
from engine.query_agent import hybrid_search_vault_func, search_internet

@pytest.mark.asyncio
async def test_hybrid_search_raw_folder_and_scoring():
    vault_path = get_vault_path()
    
    # 1. Crea una mail temporanea nella cartella raw/mail/
    test_raw_dir = os.path.join(vault_path, "raw", "mail", "test_temp_mail")
    os.makedirs(test_raw_dir, exist_ok=True)
    test_raw_file = os.path.join(test_raw_dir, "test_raw_email_12345.md")
    
    # 2. Crea un file temporaneo nella cartella wiki/sources/
    test_wiki_dir = os.path.join(vault_path, "wiki", "sources", "test_temp_wiki")
    os.makedirs(test_wiki_dir, exist_ok=True)
    test_wiki_file = os.path.join(test_wiki_dir, "test_wiki_page_12345.md")
    
    raw_content = """---
subject: "Oggetto di test della mail grezza"
from: "mittente@test.com"
---
Ecco il testo della mail che contiene la parola chiave unica LetiziaGuglielmiTest2026.
Questa informazione si trova solo in questo file raw.
"""

    wiki_content = """---
type: source
original_file: raw/mail/test_temp_mail/test_raw_email_12345.md
---
# Test Wiki Page 12345
Ecco il testo del wiki che contiene la parola chiave unica LetiziaGuglielmiTest2026.
Questa informazione si trova nella wiki.
"""
    try:
        with open(test_raw_file, "w", encoding="utf-8") as f:
            f.write(raw_content)
            
        with open(test_wiki_file, "w", encoding="utf-8") as f:
            f.write(wiki_content)
            
        # Esegue la ricerca ibrida
        results = await hybrid_search_vault_func("LetiziaGuglielmiTest2026", limit=5)
        
        # Verifica che il file wiki sia stato trovato, ma il file raw NO
        assert len(results) > 0
        
        found_wiki = False
        found_raw = False
        for r in results:
            if "test_wiki_page_12345" in r["path"]:
                found_wiki = True
            if "test_raw_email_12345" in r["path"]:
                found_raw = True
                
        assert found_wiki, f"Il file in wiki/ non è stato trovato: {results}"
        assert not found_raw, f"Il file in raw/ è stato erroneamente trovato: {results}"
        
    finally:
        # Pulisce i file temporanei
        if os.path.exists(test_raw_file):
            os.remove(test_raw_file)
        if os.path.exists(test_raw_dir):
            shutil.rmtree(test_raw_dir)
            
        if os.path.exists(test_wiki_file):
            os.remove(test_wiki_file)
        if os.path.exists(test_wiki_dir):
            shutil.rmtree(test_wiki_dir)

def test_search_internet_duckduckgo():
    # Verifica che la ricerca internet restituisca risultati
    res = search_internet("Python programming language")
    
    assert "--- RISULTATI DELLA RICERCA INTERNET ---" in res
    assert "Python" in res
