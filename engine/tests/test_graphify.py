import os
import json
import tempfile
import shutil
import pytest
from engine.graphify_manager import (
    GRAPHIFY_IGNORE_CONTENT,
    GIT_HOOK_CONTENT,
    setup_graphify_integration
)
from engine.query_agent import expand_with_graph_neighbors

def test_graphify_ignore_content():
    assert "CRM/" in GRAPHIFY_IGNORE_CONTENT
    assert "journal/" in GRAPHIFY_IGNORE_CONTENT
    assert "graphify-out/" in GRAPHIFY_IGNORE_CONTENT

def test_git_hook_content():
    assert "main graphify" in GIT_HOOK_CONTENT
    assert "#!/bin/bash" in GIT_HOOK_CONTENT

def test_setup_graphify_mocked_env(monkeypatch):
    # Creiamo una directory temporanea per simulare il vault
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mocking di get_vault_path per ritornare la cartella temporanea
        monkeypatch.setattr("engine.graphify_manager.get_vault_path", lambda: tmpdir)
        
        # Crea una struttura minima per simulare il progetto (.git, wiki, settings.md)
        os.makedirs(os.path.join(tmpdir, ".git", "hooks"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "wiki"), exist_ok=True)
        
        settings_content = """---
timing:
  sync_and_ingest: "0 10 * * *"
  weekly_reflection: "0 21 * * 0"
---
# Configurazione
"""
        settings_path = os.path.join(tmpdir, "settings.md")
        with open(settings_path, "w", encoding="utf-8") as f:
            f.write(settings_content)
            
        # Mock della funzione build_all per evitare di lanciare la CLI graphify reale durante i test
        monkeypatch.setattr("engine.graphify_manager.build_all", lambda: True)
        
        # Esegui il setup
        success = setup_graphify_integration()
        assert success is True
        
        # Verifica che il file .graphifyignore sia stato creato
        assert os.path.exists(os.path.join(tmpdir, ".graphifyignore"))
        
        # Verifica che l'hook post-commit sia stato creato
        assert os.path.exists(os.path.join(tmpdir, ".git", "hooks", "post-commit"))
        
        # Verifica che settings.md sia stato aggiornato con graphify_update
        with open(settings_path, "r", encoding="utf-8") as f:
            updated_settings = f.read()
        assert "graphify_update:" in updated_settings

def test_expand_with_graph_neighbors_with_mock_graph(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        # Prepariamo un grafo mock
        graph_dir = os.path.join(tmpdir, "wiki", "graphify-out")
        os.makedirs(graph_dir, exist_ok=True)
        
        graph_data = {
            "nodes": [
                {"id": "A", "path": "wiki/concepts/NotaA.md", "title": "Nota A"},
                {"id": "B", "path": "wiki/concepts/NotaB.md", "title": "Nota B"},
                {"id": "C", "path": "wiki/concepts/NotaC.md", "title": "Nota C"}
            ],
            "links": [
                {"source": "A", "target": "B", "type": "link"},
                {"source": "B", "target": "C", "type": "link"}
            ]
        }
        
        with open(os.path.join(graph_dir, "graph.json"), "w", encoding="utf-8") as f:
            json.dump(graph_data, f)
            
        # Mock delle funzioni per evitare di accedere al vault reale
        monkeypatch.setattr("engine.ingest_agent.load_aliases_map", lambda v: {
            "nota b": {"path": "wiki/concepts/NotaB.md", "canonical": "Nota B"},
            "nota c": {"path": "wiki/concepts/NotaC.md", "canonical": "Nota C"}
        })
        
        # Mock di parse_markdown
        monkeypatch.setattr("engine.query_agent.parse_markdown", lambda c: ({}, "Contenuto nota"))
        
        # Crea le note fittizie
        os.makedirs(os.path.join(tmpdir, "wiki", "concepts"), exist_ok=True)
        with open(os.path.join(tmpdir, "wiki", "concepts", "NotaA.md"), "w", encoding="utf-8") as f:
            f.write("# Nota A\nContenuto nota A")
        with open(os.path.join(tmpdir, "wiki", "concepts", "NotaB.md"), "w", encoding="utf-8") as f:
            f.write("# Nota B\nContenuto nota B")
            
        # Risultati di partenza (abbiamo trovato Nota A)
        results = [{"path": "wiki/concepts/NotaA.md", "title": "Nota A", "snippet": "Snippet A"}]
        
        # Esegui l'espansione
        expanded = expand_with_graph_neighbors(results, tmpdir, max_neighbors=2)
        
        # Dovrebbe aver inserito Nota B (collegata a Nota A)
        assert len(expanded) > 1
        assert any(r["title"] == "Nota B" for r in expanded)
