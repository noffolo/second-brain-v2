import os
import json
from sqlalchemy import Column, String, Float, Integer, Boolean, LargeBinary, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# Verifica se usare pgvector (PostgreSQL) o BLOB (SQLite) per la compatibilità
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///engine/second_brain.db")
is_postgres = DATABASE_URL.startswith("postgresql")

if is_postgres:
    try:
        from pgvector.sqlalchemy import Vector
        EMBEDDING_TYPE = Vector(1536)
    except ImportError:
        EMBEDDING_TYPE = LargeBinary
else:
    EMBEDDING_TYPE = LargeBinary

class Node(Base):
    """
    Rappresenta l'indice strutturato e vettoriale di un file markdown nel vault.
    """
    __tablename__ = "nodes"

    path = Column(String, primary_key=True)
    title = Column(String, nullable=False, index=True)
    type = Column(String, nullable=False, index=True)  # concept, entity, crm_contact, synthesis, journal
    tags = Column(Text, nullable=False, default="[]")  # Stringa JSON array
    related = Column(Text, nullable=False, default="[]")  # Stringa JSON array
    parent = Column(String, nullable=True)
    aliases = Column(Text, nullable=False, default="[]")  # Stringa JSON array
    mtime = Column(Float, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(EMBEDDING_TYPE, nullable=True)

    def get_tags_list(self) -> list[str]:
        try:
            return json.loads(self.tags)
        except Exception:
            return []

    def set_tags_list(self, val: list[str]):
        self.tags = json.dumps(val or [])

    def get_related_list(self) -> list[str]:
        try:
            return json.loads(self.related)
        except Exception:
            return []

    def set_related_list(self, val: list[str]):
        self.related = json.dumps(val or [])

    def get_aliases_list(self) -> list[str]:
        try:
            return json.loads(self.aliases)
        except Exception:
            return []

    def set_aliases_list(self, val: list[str]):
        self.aliases = json.dumps(val or [])


class EpisodicMemory(Base):
    """
    Conserva lo storico conversazionale per i contesti degli agenti.
    """
    __tablename__ = "episodic_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String, nullable=False, index=True)
    sender = Column(String, nullable=False)  # user o assistant
    message = Column(Text, nullable=False)
    timestamp = Column(Float, nullable=False)


class ProceduralConfig(Base):
    """
    Configurazioni operative degli agenti AI (prompt e modelli).
    """
    __tablename__ = "procedural_config"

    agent_name = Column(String, primary_key=True)
    system_instructions = Column(Text, nullable=False)
    primary_model = Column(String, nullable=False)
    fallback_chain = Column(Text, nullable=False, default="[]")  # Stringa JSON array

    def get_fallback_list(self) -> list[str]:
        try:
            return json.loads(self.fallback_chain)
        except Exception:
            return []

    def set_fallback_list(self, val: list[str]):
        self.fallback_chain = json.dumps(val or [])


class ScheduledJob(Base):
    """
    Automazioni e pianificazioni gestite dinamicamente dal backend.
    """
    __tablename__ = "scheduled_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    schedule_type = Column(String, nullable=False)  # time (es. '09:00') o interval (in minuti)
    schedule_value = Column(String, nullable=False)  # es. '09:00' o '30'
    target_action = Column(String, nullable=False)  # es. 'ingest', 'ontology', 'reflect', o il nome di un agente
    last_run = Column(Float, nullable=True)
    is_active = Column(Integer, nullable=False, default=1)  # 1 = attivo, 0 = disattivo
