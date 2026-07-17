from pydantic import BaseModel, ConfigDict


class PolicyChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    title: str
    section: str
    text: str


class SearchHit(PolicyChunk):
    score: float
