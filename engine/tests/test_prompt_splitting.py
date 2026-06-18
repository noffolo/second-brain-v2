import re
import pytest
from engine.dashboard import parse_agents_md, rebuild_agents_md

def test_prompt_parsing_and_rebuilding():
    original_content = """# Istruzioni Operative per gli Agenti AI

Questo file definisce le istruzioni di sistema per i vari agenti del Secondo Cervello.

---

## Identity (Linee Guida Generali)

Sei l'assistente del Secondo Cervello dell'utente.

---

## Ingest Agent

Il tuo compito è analizzare nuovi file.

---

## Query Agent

Sei l'interfaccia interattiva.
"""
    
    sections = parse_agents_md(original_content)
    assert "Identity (Linee Guida Generali)" in sections
    assert "Ingest Agent" in sections
    assert "Query Agent" in sections
    
    assert sections["Identity (Linee Guida Generali)"] == "Sei l'assistente del Secondo Cervello dell'utente."
    assert sections["Ingest Agent"] == "Il tuo compito è analizzare nuovi file."
    
    # Modifichiamo una sezione
    sections["Ingest Agent"] = "Il tuo compito è analizzare file in modo super ottimizzato."
    
    rebuilt = rebuild_agents_md(sections, original_content)
    
    assert "Istruzioni Operative per gli Agenti AI" in rebuilt
    assert "Identity (Linee Guida Generali)" in rebuilt
    assert "Sei l'assistente del Secondo Cervello dell'utente." in rebuilt
    assert "Ingest Agent" in rebuilt
    assert "Il tuo compito è analizzare file in modo super ottimizzato." in rebuilt
    
    # Verifichiamo che Query Agent sia rimasta inalterata
    assert "Sei l'interfaccia interattiva." in rebuilt
