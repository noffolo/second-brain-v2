from abc import ABC, abstractmethod

class BaseVectorStore(ABC):
    @abstractmethod
    async def add_embeddings(self, path: str, embedding: list[float]) -> None:
        """Salva l'embedding vettoriale per un nodo specifico"""
        pass

    @abstractmethod
    async def search_similarity(self, query_vector: list[float], limit: int = 5, filters: dict = None) -> list[dict]:
        """Trova i nodi più vicini per similarità coseno"""
        pass

class BaseScheduler(ABC):
    @abstractmethod
    def add_job(self, name: str, schedule_type: str, schedule_value: str, target_action: str) -> int:
        """Aggiunge un job schedulato e ritorna il suo ID"""
        pass

    @abstractmethod
    def run_pending_jobs(self) -> None:
        """Esegue tutti i job che sono pronti per essere eseguiti"""
        pass
