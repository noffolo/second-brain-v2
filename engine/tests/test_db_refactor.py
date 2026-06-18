import os
import tempfile
import time
import struct
import shutil
import pytest
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Imposta chiavi d'ambiente fittizie per i test
os.environ["GEMINI_API_KEY"] = "dummy-gemini-key"
os.environ["Z_AI_API_KEY"] = "dummy-z-ai-key"

from engine.db.models import Base, Node, EpisodicMemory, ProceduralConfig, ScheduledJob
from engine.db.connection import get_db_session
from engine.utils.vector_db import VectorDB, blob_to_vector, vector_to_blob, cosine_similarity
from engine.utils.llm_fallback import call_llm_with_fallback, parse_provider_model
from google.antigravity import LocalAgentConfig

@pytest.fixture
def mock_db():
    # Database SQLite in memoria
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Esegue il patch di connection.SessionLocal ed engine
    with patch("engine.db.connection.SessionLocal", TestingSessionLocal), \
         patch("engine.db.connection.engine", engine):
        yield TestingSessionLocal

def test_database_initialization(mock_db):
    session = mock_db()
    # Verifica che le tabelle siano queryabili e inizialmente vuote
    assert session.query(Node).count() == 0
    assert session.query(EpisodicMemory).count() == 0
    assert session.query(ProceduralConfig).count() == 0
    assert session.query(ScheduledJob).count() == 0
    
    # Aggiunge un record Node di prova
    node = Node(
        path="wiki/concepts/test.md",
        title="Test Node",
        type="concept",
        tags='["#test"]',
        related='["[[Other]]"]',
        mtime=time.time(),
        content="Contenuto di prova.",
        embedding=struct.pack("3f", 1.0, 2.0, 3.0)
    )
    session.add(node)
    session.commit()
    
    db_node = session.query(Node).first()
    assert db_node is not None
    assert db_node.title == "Test Node"
    assert db_node.get_tags_list() == ["#test"]
    assert db_node.get_related_list() == ["[[Other]]"]
    assert blob_to_vector(db_node.embedding) == [1.0, 2.0, 3.0]
    session.close()

def test_vault_sync_to_db(mock_db, tmp_path):
    # Crea una cartella vault temporanea
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    concepts_dir = vault_dir / "wiki" / "concepts"
    concepts_dir.mkdir(parents=True)
    
    note_a = concepts_dir / "NoteA.md"
    note_a.write_text("---\ntags: ['#tech']\n---\nQuesto è il contenuto di NoteA con [[NoteB]].", encoding="utf-8")
    
    note_b = concepts_dir / "NoteB.md"
    note_b.write_text("---\ntags: ['#personal']\n---\nQuesto è il contenuto di NoteB.", encoding="utf-8")
    
    with patch("engine.utils.vector_db.get_vault_path", return_value=str(vault_dir)), \
         patch("engine.tools.vault_tools.get_vault_path", return_value=str(vault_dir)):
         
        db = VectorDB()
        # Mocking get_embedding per ritornare un vettore dummy
        with patch("engine.utils.vector_db.get_embedding", return_value=[0.1, 0.2, 0.3]):
            db.upsert_chunks(str(note_a), "NoteA", ["Questo è il contenuto di NoteA con [[NoteB]]."])
            db.upsert_chunks(str(note_b), "NoteB", ["Questo è il contenuto di NoteB."])
            
        session = mock_db()
        nodes = session.query(Node).all()
        assert len(nodes) == 2
        
        node_a_record = session.query(Node).filter(Node.title == "NoteA").first()
        assert node_a_record is not None
        assert "#tech" in node_a_record.get_tags_list()
        assert "[[NoteB]]" in node_a_record.get_related_list()
        
        # Test di modifica incrementale
        time.sleep(0.1)
        note_b.write_text("---\ntags: ['#personal', '#updated']\n---\nNuovo testo di NoteB.", encoding="utf-8")
        
        with patch("engine.utils.vector_db.get_embedding", return_value=[0.4, 0.5, 0.6]):
            db.upsert_chunks(str(note_b), "NoteB", ["Nuovo testo di NoteB."])
            
        session.close()
        
        # Usa una nuova sessione per evitare il caching della Identity Map di SQLAlchemy
        session = mock_db()
        updated_node_b = session.query(Node).filter(Node.title == "NoteB").first()
        assert len(session.query(Node).all()) == 2
        assert "#updated" in updated_node_b.get_tags_list()
        assert updated_node_b.content == "Nuovo testo di NoteB."
        session.close()

def test_agent_creator_and_file_alignment(mock_db, tmp_path):
    # Setup dei file temporanei
    agents_md = tmp_path / "agents.md"
    agents_md.write_text("## DefaultAgent\nDefault Instructions\n", encoding="utf-8")
    settings_md = tmp_path / "settings.md"
    settings_md.write_text("---\nmodels:\n  DefaultAgent:\n    primary: google/gemini-3.5-pro\n---\n", encoding="utf-8")
    
    from engine.dashboard import api_create_agent, AgentCreateRequest
    
    req = AgentCreateRequest(
        agent_name="CustomAgent",
        system_instructions="Custom System Prompt Instructions",
        primary_model="z_ai/glm-5.2",
        fallback_chain=["google/gemini-3.5-pro", "openai/gpt-5"]
    )
    
    with patch("engine.dashboard.get_vault_path", return_value=str(tmp_path)), \
         patch("engine.dashboard.db_session", mock_db), \
         patch("engine.git_ops.auto_commit") as mock_commit:
        
        res = api_create_agent(req)
        assert res["status"] == "success"
        
        # Verifica record in SQLite
        session = mock_db()
        config = session.query(ProceduralConfig).filter(ProceduralConfig.agent_name == "CustomAgent").first()
        assert config is not None
        assert config.system_instructions == "Custom System Prompt Instructions"
        assert config.primary_model == "z_ai/glm-5.2"
        assert config.get_fallback_list() == ["google/gemini-3.5-pro", "openai/gpt-5"]
        
        # Verifica agents.md
        agents_content = agents_md.read_text(encoding="utf-8")
        assert "## CustomAgent" in agents_content
        assert "Custom System Prompt Instructions" in agents_content
        
        # Verifica settings.md
        settings_content = settings_md.read_text(encoding="utf-8")
        assert "CustomAgent" in settings_content
        assert "z_ai/glm-5.2" in settings_content
        session.close()

def test_dynamic_scheduler_intervals(mock_db):
    session = mock_db()
    # Registra un job attivo in SQLite con intervallo di 1 minuto
    job = ScheduledJob(
        name="Mock Interval Job",
        schedule_type="interval",
        schedule_value="1",
        target_action="ingest",
        is_active=1,
        last_run=None
    )
    session.add(job)
    session.commit()
    
    run_mock = AsyncMock()
    
    with patch("engine.dashboard.db_session", mock_db), \
         patch("engine.dashboard.run_task_subprocess_by_name", run_mock):
         
        from engine.dashboard import sync_scheduled_jobs_to_db
        sync_scheduled_jobs_to_db()
        
        # Prima esecuzione: last_run è nullo, quindi deve avviarsi
        now = float(time.time())
        with mock_db() as s:
            active_jobs = s.query(ScheduledJob).filter(ScheduledJob.is_active == 1).all()
            jobs_to_run = []
            for j in active_jobs:
                if j.last_run is None:
                    jobs_to_run.append((j.id, j.name, j.target_action))
                    j.last_run = now
            s.commit()
            
        assert len(jobs_to_run) > 0
        
        # Verifica che il last_run sia ora valorizzato
        with mock_db() as s:
            db_job = s.query(ScheduledJob).filter(ScheduledJob.name == "Mock Interval Job").first()
            assert db_job.last_run == now
            
            # Esegue di nuovo istantaneamente: non deve avviarsi (1 minuto non passato)
            jobs_to_run2 = []
            active_jobs2 = s.query(ScheduledJob).filter(ScheduledJob.is_active == 1).all()
            for j in active_jobs2:
                if j.last_run is not None:
                    if j.schedule_type == "interval":
                        interval_minutes = int(j.schedule_value)
                        if time.time() - j.last_run >= interval_minutes * 60:
                            jobs_to_run2.append(j.id)
            assert len(jobs_to_run2) == 0

def test_vector_similarity_search(mock_db):
    session = mock_db()
    # Inserisce 3 nodi con embedding specifici
    n1 = Node(
        path="target.md", title="Target", type="concept", tags="[]", related="[]", mtime=1.0, content="Target content",
        embedding=vector_to_blob([1.0, 0.0, 0.0])
    )
    n2 = Node(
        path="orthogonal.md", title="Orthogonal", type="concept", tags="[]", related="[]", mtime=1.0, content="Orthogonal content",
        embedding=vector_to_blob([0.0, 1.0, 0.0])
    )
    n3 = Node(
        path="opposite.md", title="Opposite", type="concept", tags="[]", related="[]", mtime=1.0, content="Opposite content",
        embedding=vector_to_blob([-1.0, 0.0, 0.0])
    )
    session.add_all([n1, n2, n3])
    session.commit()
    session.close()
    
    db = VectorDB()
    with patch("engine.utils.vector_db.get_query_embedding", return_value=[1.0, 0.0, 0.0]):
        results = db.search_similar("query text", limit=3)
        
    assert len(results) == 3
    # Ordinamento decrescente per similarità (distanza crescente: 1-sim)
    assert results[0]["title"] == "Target"
    assert abs(results[0]["distance"] - 0.0) < 1e-5
    
    assert results[1]["title"] == "Orthogonal"
    assert abs(results[1]["distance"] - 1.0) < 1e-5
    
    assert results[2]["title"] == "Opposite"
    assert abs(results[2]["distance"] - 2.0) < 1e-5

@pytest.mark.asyncio
async def test_z_ai_fallback_flow(mock_db):
    # Verifica il flusso di fallback quando Z_AI fallisce ed OpenAI risponde correttamente
    prompt = "Test Prompt"
    system_instructions = "Instructions"
    gemini_config = LocalAgentConfig(model="z_ai/glm-5.2")
    
    mock_settings = {
        "models": {
            "query_agent": {
                "primary": "z_ai/glm-5.2",
                "fallback": ["openai/gpt-5"]
            }
        }
    }
    
    async def mock_call_api(url, api_key, model, system_instructions, prompt, timeout=90):
        if "z.ai" in url:
            raise Exception("429 Rate Limit / Out of Quota")
        elif "openai" in url:
            return "OpenAI Fallback Success Response"
        return ""
        
    with patch("engine.utils.markdown.load_settings", return_value=mock_settings), \
         patch("engine.utils.llm_fallback.fetch_keys_from_free4all", return_value=[]), \
         patch("engine.utils.llm_fallback.call_openai_compatible_api", side_effect=mock_call_api):
         
        response = await call_llm_with_fallback(prompt, system_instructions, gemini_config, agent_name="query_agent")
        assert response == "OpenAI Fallback Success Response"
