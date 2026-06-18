import os
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from engine.db.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///engine/second_brain.db")

# Assicura che la directory contenente il database SQLite esista
if DATABASE_URL.startswith("sqlite:///"):
    db_path = DATABASE_URL.replace("sqlite:///", "")
    # Se il percorso è relativo, risolvilo rispetto alla root del progetto
    if not os.path.isabs(db_path):
        from engine.tools.vault_tools import get_vault_path
        db_path = os.path.join(get_vault_path(), db_path)
    
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

# Crea l'engine del database
engine = create_engine(
    DATABASE_URL,
    # Impostazioni specifiche per SQLite per gestire al meglio il multi-threading locale
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite:///") else {}
)

# Fabbrica per le sessioni del database
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """
    Crea tutte le tabelle nel database se non esistono già.
    """
    Base.metadata.create_all(bind=engine)

def get_db_session() -> Session:
    """
    Restituisce una nuova sessione di database.
    Deve essere chiusa manualmente.
    """
    return SessionLocal()

@contextmanager
def db_session():
    """
    Gestore di contesto per le sessioni del database.
    Esegue automaticamente il commit in caso di successo e rollback in caso di errore.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
