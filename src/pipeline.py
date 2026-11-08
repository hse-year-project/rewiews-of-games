import logging
import threading
from datetime import datetime

import pandas as pd

from src.clean.run import clean_reviews
from src.collect.run import collect, parse
from src.embeddings import embed_new, load_encoder

log = logging.getLogger(__name__)


def run(jobs: pd.DataFrame, model="BAAI/bge-m3", every_minutes=10, embed=True) -> pd.DataFrame:
    """Collect `jobs` (from collect.run.plan) and meanwhile parse, clean and embed what has arrived.

    Collection mostly waits on Steam's request limit, so it runs in a background thread while
    this thread processes the new data every `every_minutes`, and once more when it is done.
    Interrupting stops both. Returns collect()'s result per job.
    """
    stop = threading.Event()
    outcome = {}

    def collect_all():
        try:
            outcome["result"] = collect(jobs, stop)
        except Exception as e:  # re-raised below, after processing what was collected
            outcome["error"] = e

    collector = threading.Thread(target=collect_all, name="collect", daemon=True)
    collector.start()
    encoder = load_encoder(model) if embed else None
    try:
        while True:
            collector.join(timeout=every_minutes * 60)
            finished = not collector.is_alive()
            process(model, encoder, final=finished)
            if finished:
                break
    except KeyboardInterrupt:
        stop.set()
        collector.join()
        raise
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def process(model: str, encoder=None, final=False):
    """One pass over what is new: parse raw pages, clean reviews, embed kept reviews.
    The cleaning report over all reviews is only made on the final pass."""
    print(f"--- {datetime.now():%H:%M:%S} processing new data")
    parse()
    clean_reviews(report=final)
    if encoder is not None:
        embed_new(model, encoder)
