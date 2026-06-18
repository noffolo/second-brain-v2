import os
import shutil
import tempfile
from unittest.mock import patch
from engine.ontology_agent import merge_nodes, rollback_ontology_proposal, set_parent, connect_nodes
from engine.utils.markdown import parse_markdown

def test_ontology_merge_and_rollback():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Crea la struttura del vault nel tmpdir
        wiki_concepts_dir = os.path.join(tmpdir, "wiki", "concepts")
        os.makedirs(wiki_concepts_dir, exist_ok=True)
        
        # Crea i file di test
        path_a_rel = "wiki/concepts/A"
        path_b_rel = "wiki/concepts/B"
        path_c_rel = "wiki/concepts/C"
        
        abs_a = os.path.join(tmpdir, path_a_rel + ".md")
        abs_b = os.path.join(tmpdir, path_b_rel + ".md")
        abs_c = os.path.join(tmpdir, path_c_rel + ".md")
        
        with open(abs_a, "w", encoding="utf-8") as f:
            f.write("---\ntype: concept\ntags: [tagA]\nrelated: []\n---\n# A\nContenuto di A.")
            
        with open(abs_b, "w", encoding="utf-8") as f:
            f.write("---\ntype: concept\ntags: [tagB]\nrelated: []\n---\n# B\nContenuto di B.")
            
        with open(abs_c, "w", encoding="utf-8") as f:
            f.write("---\ntype: concept\ntags: []\nrelated: []\n---\n# C\nQuesto fa riferimento a [[wiki/concepts/A|Nota A]] e [[wiki/concepts/A]].")
            
        # Mocking get_vault_path
        with patch("engine.ontology_agent.get_vault_path", return_value=tmpdir), \
             patch("engine.ontology_agent.append_to_log"), \
             patch("engine.ontology_agent.auto_commit"):
            
            # Eseguiamo la fusione con backup
            success = merge_nodes(tmpdir, path_a_rel, path_b_rel, "M1")
            assert success is True
            
            # Verifichiamo lo stato post-fusione
            assert not os.path.exists(abs_a) # A rimosso
            
            with open(abs_b, "r", encoding="utf-8") as f:
                fm_b, body_b = parse_markdown(f.read())
            assert "Contenuto di A." in body_b # Contenuto unificato
            assert "tagA" in fm_b.get("tags", []) # Tag unificati
            assert "A" in fm_b.get("aliases", []) # Alias aggiunto
            
            with open(abs_c, "r", encoding="utf-8") as f:
                _, body_c = parse_markdown(f.read())
            assert "[[wiki/concepts/B|Nota A]]" in body_c # Link aggiornato
            assert "[[wiki/concepts/B|B]]" in body_c # Link corto aggiornato
            
            # Verifichiamo che la cartella di backup esista
            backup_dir = os.path.join(tmpdir, "engine", "ontology_backups", "M1")
            assert os.path.exists(backup_dir)
            assert os.path.exists(os.path.join(backup_dir, path_a_rel + ".md"))
            assert os.path.exists(os.path.join(backup_dir, path_b_rel + ".md"))
            assert os.path.exists(os.path.join(backup_dir, path_c_rel + ".md"))
            
            # Ora eseguiamo il rollback
            rb_success = rollback_ontology_proposal("M1")
            assert rb_success is True
            
            # Verifichiamo lo stato post-rollback (ripristinato all'istante)
            assert os.path.exists(abs_a)
            with open(abs_a, "r", encoding="utf-8") as f:
                fm_a, body_a = parse_markdown(f.read())
            assert "Contenuto di A." in body_a
            assert fm_a.get("tags") == ["tagA"]
            
            with open(abs_b, "r", encoding="utf-8") as f:
                fm_b_orig, body_b_orig = parse_markdown(f.read())
            assert "Contenuto di A." not in body_b_orig
            assert fm_b_orig.get("tags") == ["tagB"]
            assert "aliases" not in fm_b_orig or "A" not in fm_b_orig.get("aliases")
            
            with open(abs_c, "r", encoding="utf-8") as f:
                _, body_c_orig = parse_markdown(f.read())
            assert "[[wiki/concepts/A|Nota A]]" in body_c_orig
            assert "[[wiki/concepts/A]]" in body_c_orig
            
            # Il backup deve essere stato rimosso
            assert not os.path.exists(backup_dir)
