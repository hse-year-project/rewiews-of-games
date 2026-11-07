from datetime import datetime

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import torch
from sentence_transformers import SentenceTransformer
from tqdm.auto import tqdm

from src.config import EMBEDDINGS_DIR
from src.db import read


class Embeddings:
    """Review vectors of one model in data/embeddings/<model>/part-*.parquet.

    A review edited after it was embedded has several vectors; only the newest counts.
    Files are read one at a time, so all vectors are never in memory at once.
    """

    def __init__(self, model: str):
        self.dir = EMBEDDINGS_DIR / model.replace("/", "__")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.parts = sorted(self.dir.glob("part-*.parquet"))
        files = [
            pd.read_parquet(p, columns=["recommendation_id", "updated_at"]).assign(part=i)
            for i, p in enumerate(self.parts)
        ]
        index = pd.concat(files, ignore_index=True) if files else _empty_index()
        index["row"] = index.groupby("part").cumcount()
        # recommendation_id, updated_at (review's, when embedded), part (file), row (in the file)
        self.index = index.sort_values("updated_at").drop_duplicates(
            "recommendation_id", keep="last"
        )
        self.run = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.written = 0

    def vectors(self, ids=None):
        """(ids, float32 vectors) per file for the given review IDs (None = all)."""
        rows = self.index if ids is None else self.index[self.index["recommendation_id"].isin(ids)]
        for part, group in rows.groupby("part"):
            table = pq.read_table(self.parts[part], columns=["recommendation_id", "embedding"])
            table = table.take(np.sort(group["row"].to_numpy()))
            flat = table["embedding"].combine_chunks().flatten().to_numpy()
            yield (
                table["recommendation_id"].to_numpy(),
                flat.reshape(len(table), -1).astype(np.float32),
            )

    def matrix(self, ids) -> np.ndarray:
        """Vectors of these reviews, one row per ID in the given order (all must be embedded)."""
        found, vectors = map(np.concatenate, zip(*self.vectors(ids), strict=True))
        return vectors[pd.Index(found).get_indexer(ids)]

    def add(self, ids, updated_at, vectors: np.ndarray):
        """Save vectors as a new file (via a temporary name: a stop leaves no broken file)."""
        table = pa.table(
            {
                "recommendation_id": pa.array(ids),
                "updated_at": pa.array(updated_at),
                "embedding": pa.FixedSizeListArray.from_arrays(
                    vectors.astype(np.float16).ravel(), vectors.shape[1]
                ),
            }
        )
        path = self.dir / f"part-{self.run}-{self.written:04d}.parquet"
        pq.write_table(table, path.with_suffix(".tmp"))
        path.with_suffix(".tmp").rename(path)
        self.written += 1


def load_encoder(model: str) -> SentenceTransformer:
    """The model on the GPU in float16 if there is one (on a CPU it works, but slowly)."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    encoder = SentenceTransformer(model, device=device, model_kwargs={"dtype": dtype})
    encoder.max_seq_length = 512
    return encoder


def embed_new(model: str, encoder, prompt="", batch=128, part_size=20_000) -> int:
    """Embed kept reviews that are new or edited since they were embedded; returns how many.

    Texts are loaded per part of `part_size` reviews, and each part is saved as one file,
    so a stop loses at most one part and re-running continues where it stopped.
    """
    emb = Embeddings(model)
    reviews = read.reviews(
        columns=["recommendation_id", "updated_at"], only_kept=True, flag_columns=[]
    )
    embedded = emb.index[["recommendation_id", "updated_at"]].rename(
        columns={"updated_at": "embedded_at"}
    )
    reviews = reviews.merge(embedded, on="recommendation_id", how="left")
    todo = reviews[reviews["embedded_at"].isna() | (reviews["updated_at"] > reviews["embedded_at"])]
    print(f"embedding {len(todo):,} new or edited reviews ({len(emb.index):,} already embedded)")

    with tqdm(total=len(todo), desc="embed", unit="review") as bar:
        for start in range(0, len(todo), part_size):
            part = todo.iloc[start : start + part_size]
            texts = read.reviews(
                ids=part["recommendation_id"],
                columns=["recommendation_id", "review"],
                with_flags=False,
            )
            part = part.merge(texts, on="recommendation_id").fillna({"review": ""})
            # similar lengths in one batch: less padding
            part = part.iloc[np.argsort(part["review"].str.len().to_numpy())]
            vectors = []
            for b in range(0, len(part), batch):
                texts = part["review"].iloc[b : b + batch].tolist()
                vectors.append(
                    encoder.encode(
                        texts, prompt=prompt, batch_size=batch, normalize_embeddings=True
                    )
                )
                bar.update(len(texts))
            emb.add(part["recommendation_id"], part["updated_at"], np.concatenate(vectors))
    return len(todo)


def _empty_index():
    return pd.DataFrame(
        {
            "recommendation_id": pd.Series(dtype="int64"),
            "updated_at": pd.Series(dtype="datetime64[us, UTC]"),
            "part": pd.Series(dtype="int64"),
        }
    )
