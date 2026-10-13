"""search_runbooks: semantic search over runbooks/*.md with Chroma + a small local embedding model.

The embedding model and the Chroma collection are loaded once per process (they take a
few seconds), then reused by every call.
"""

import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from rich.console import Console

from triage.config import get_settings

COLLECTION = "runbooks"


class SearchRunbooksArgs(BaseModel):
    """Find the runbooks that best match a description of the symptoms or suspected fault."""

    query: str = Field(min_length=3, max_length=300, description="symptoms or suspected fault, in words")
    k: int = Field(default=3, ge=1, le=3, description="how many runbooks to return (max 3)")


def parse_runbook(path: Path) -> dict:
    """Split a runbook into its front matter fields and its body text."""
    text = path.read_text()
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError(f"{path} has no front matter")
    meta = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    meta["allowed_actions"] = [a.strip() for a in meta["allowed_actions"].strip("[]").split(",") if a.strip()]
    meta["body"] = match.group(2).strip()
    return meta


def load_runbooks(directory: Path | None = None) -> dict[str, dict]:
    directory = directory or get_settings().runbooks_dir
    books = [parse_runbook(p) for p in sorted(directory.glob("*.md"))]
    return {b["id"]: b for b in books}


def _symptoms(body: str) -> str:
    """The Symptoms section is what we embed and show: it describes what an engineer would see."""
    match = re.search(r"## Symptoms\n(.*?)(\n## |\Z)", body, re.DOTALL)
    return (match.group(1) if match else body).strip()


@lru_cache(maxsize=1)
def _embedder():
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    return SentenceTransformerEmbeddingFunction(model_name=get_settings().embedding_model, device="cpu")


@lru_cache(maxsize=1)
def _collection():
    import chromadb

    settings = get_settings()
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    metadata = {"hnsw:space": "cosine", "embedding_model": settings.embedding_model}
    collection = client.get_or_create_collection(COLLECTION, embedding_function=_embedder(), metadata=metadata)
    # Rebuild when the runbook files or the embedding model changed since the index was made.
    stale = set(collection.get()["ids"]) != set(load_runbooks())
    stale |= (collection.metadata or {}).get("embedding_model") != settings.embedding_model
    if stale:
        client.delete_collection(COLLECTION)
        collection = client.create_collection(COLLECTION, embedding_function=_embedder(), metadata=metadata)
        _fill(collection)
    return collection


def _fill(collection) -> None:
    books = load_runbooks()
    collection.add(
        ids=list(books),
        documents=[f"{b['title']}. {_symptoms(b['body'])}" for b in books.values()],
        metadatas=[
            {"title": b["title"], "fault_type": b["fault_type"], "allowed_actions": ",".join(b["allowed_actions"])}
            for b in books.values()
        ],
    )


def index_runbooks(rebuild: bool = False) -> int:
    """(Re)build the persistent index under .cache/chroma. Returns the number of runbooks."""
    import chromadb

    settings = get_settings()
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    if rebuild and COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    _collection.cache_clear()
    count = _collection().count()
    Console().print(f"Indexed {count} runbooks into {settings.chroma_dir}")
    return count


def search(query: str, k: int = 3) -> list[dict]:
    """Top-k runbooks with metadata and a cosine similarity score (1 = identical)."""
    result = _collection().query(query_texts=[query], n_results=k)
    hits = []
    for rb_id, doc, meta, dist in zip(
        result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
    ):
        hits.append(
            {
                "id": rb_id,
                "title": meta["title"],
                "fault_type": meta["fault_type"],
                "allowed_actions": meta["allowed_actions"].split(","),
                "snippet": " ".join(doc.split())[:200],
                "score": round(1 - dist, 3),
            }
        )
    return hits


def render_hits(query: str, hits: list[dict]) -> str:
    """Short text lines the model can quote."""
    lines = [f"runbooks matching: {query}"]
    for hit in hits:
        lines.append(
            f"- {hit['id']} | {hit['title']} | fault_type={hit['fault_type']} | "
            f"allowed_actions={','.join(hit['allowed_actions'])} | score={hit['score']}"
        )
        lines.append(f"  {hit['snippet']}")
    return "\n".join(lines)


def search_runbooks(ctx, args: SearchRunbooksArgs) -> str:
    """Tool entry point."""
    return render_hits(args.query, search(args.query, args.k))


# Symptom phrases written in different words from both the alerts and the runbooks.
RETRIEVAL_CHECKS = [
    ("the container is pegged at its processor limit and requests pile up behind it", "rb-cpu-saturation"),
    ("resident set keeps climbing until the pod gets oom killed", "rb-memory-leak"),
    ("calls into one service fail intermittently and need retries, connections reset", "rb-packet-loss"),
    ("every hop into the service takes much longer although it does no extra work", "rb-network-latency"),
    ("huge amount of bytes written to the filesystem, io wait is high", "rb-disk-pressure"),
    ("too many open connections, new ones are refused", "rb-socket-exhaustion"),
]


def check_retrieval() -> bool:
    console = Console()
    ok = True
    for phrase, expected in RETRIEVAL_CHECKS:
        top = search(phrase, k=1)[0]
        good = top["id"] == expected
        ok &= good
        mark = "[green]OK[/green]" if good else "[red]WRONG[/red]"
        console.print(f"{mark} '{phrase}' -> {top['id']} (score {top['score']}, expected {expected})")
    return ok
