import os
import json
import struct
import numpy as np
from typing import List, Dict, Any

from engine.tools.vault_tools import get_vault_path
from engine.tools.embedder import get_embedding, get_query_embedding
from engine.db.connection import db_session, init_db
from engine.db.models import Node
from engine.utils.markdown import parse_markdown, extract_wikilinks

def blob_to_vector(blob: bytes) -> List[float]:
    """Converte un BLOB binario in una lista di float (float32)"""
    if not blob:
        return []
    n = len(blob) // 4
    try:
        return list(struct.unpack(f"{n}f", blob))
    except Exception:
        return []

def vector_to_blob(vector: List[float]) -> bytes:
    """Converte una lista di float in un BLOB binario"""
    if not vector:
        return b""
    return struct.pack(f"{len(vector)}f", *vector)

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Calcola la similarità coseno tra due vettori"""
    a = np.array(v1)
    b = np.array(v2)
    dot_product = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot_product / (norm_a * norm_b))

class VectorDB:
    def __init__(self, collection_name="second_brain_docs"):
        # Inizializza il database SQLite (tabelle) se non esistono
        init_db()

    def upsert_chunks(self, path: str, title: str, chunks: List[str]):
        """
        Salva l'embedding vettoriale per un nodo specifico ed aggiorna
        lo stato del nodo all'interno della tabella SQLite 'nodes'.
        """
        if not chunks:
            return

        vault_path = get_vault_path()
        # Normalizza i percorsi per renderli relativi al vault
        if os.path.isabs(path):
            rel_path = os.path.relpath(path, vault_path)
            abs_path = path
        else:
            rel_path = path
            abs_path = os.path.join(vault_path, path)

        # Calcola l'embedding per il testo completo unito
        full_text = "\n".join(chunks)
        emb = get_embedding(full_text)

        title = title or os.path.splitext(os.path.basename(rel_path))[0]
        content = ""
        tags = []
        related = []
        aliases = []
        node_type = "concept"
        mtime = 0.0

        if os.path.exists(abs_path):
            mtime = os.path.getmtime(abs_path)
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    raw_content = f.read()
                fm, body = parse_markdown(raw_content)
                content = body
                node_type = fm.get("type") or fm.get("Type")
                if not node_type:
                    # Rilevamento in base alla cartella
                    lower_path = rel_path.lower()
                    if "journal" in lower_path:
                        node_type = "journal"
                    elif "meetings" in lower_path:
                        node_type = "journal"
                    elif "entities" in lower_path:
                        node_type = "entity"
                    elif "concepts" in lower_path:
                        node_type = "concept"
                    elif "crm" in lower_path:
                        node_type = "crm_contact"
                    elif "synthesis" in lower_path:
                        node_type = "synthesis"
                    else:
                        node_type = "concept"

                tags = fm.get("tags") or []
                if isinstance(tags, str):
                    tags = [tags]

                related = fm.get("related") or []
                if isinstance(related, str):
                    related = [related]
                # Aggiunge i wikilink estratti dal corpo
                wikilinks = extract_wikilinks(raw_content)
                related = list(set(related + [f"[[{w}]]" for w in wikilinks]))

                aliases = fm.get("aliases") or []
                if isinstance(aliases, str):
                    aliases = [aliases]
            except Exception as e:
                print(f"[VectorDB] Errore nel parsing del file {abs_path}: {e}")

        # Esegue l'upsert del record del nodo in SQLite
        with db_session() as session:
            node = session.query(Node).filter(Node.path == rel_path).first()
            if not node:
                node = Node(path=rel_path)
                session.add(node)

            node.title = title
            node.type = node_type
            node.set_tags_list(tags)
            node.set_related_list(related)
            node.set_aliases_list(aliases)
            node.mtime = mtime
            node.content = content
            if emb:
                node.embedding = vector_to_blob(emb)

    def search_similar(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Cerca i frammenti più simili alla query usando la Cosine Similarity ed
        estrapolando una distanza compatibile con i controlli ChromaDB (1 - similarity).
        """
        query_emb = get_query_embedding(query)
        if not query_emb:
            return []

        results = []
        with db_session() as session:
            nodes = session.query(Node).filter(Node.embedding.isnot(None)).all()
            for node in nodes:
                node_vector = blob_to_vector(node.embedding)
                if not node_vector or len(node_vector) != len(query_emb):
                    continue

                sim = cosine_similarity(query_emb, node_vector)
                distance = float(1.0 - sim)

                snippet = node.content.strip()
                if len(snippet) > 600:
                    snippet = snippet[:600] + "..."

                results.append({
                    "path": node.path,
                    "title": node.title,
                    "snippet": snippet,
                    "distance": distance
                })

        # Ordina per distanza minore (maggiore similarità)
        results.sort(key=lambda x: x["distance"])
        return results[:limit]

# Singleton instance
_vector_db_instance = None

def get_vector_db() -> VectorDB:
    global _vector_db_instance
    if _vector_db_instance is None:
        _vector_db_instance = VectorDB()
    return _vector_db_instance
