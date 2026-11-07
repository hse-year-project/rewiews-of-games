from src.config import TOPIC_NOISE, TOPIC_SHORT

FIXED_LABELS = {TOPIC_SHORT: "Too short", TOPIC_NOISE: "No clear topic"}


def topic_names(topics, with_id=True) -> dict[int, str]:
    """Name per topic id (topics from read.topics()): its label, or first keywords while unnamed.

    with_id=False: just the label, so topics sharing a label (one theme in several languages)
    get the same name and count together.
    """
    names = {}
    for t in topics.itertuples():
        if t.topic_id < 0:
            names[t.topic_id] = t.label if isinstance(t.label, str) else FIXED_LABELS[t.topic_id]
        elif isinstance(t.label, str):
            names[t.topic_id] = f"{t.topic_id} · {t.label}" if with_id else t.label
        else:
            names[t.topic_id] = f"{t.topic_id} · " + ", ".join(str(t.keywords).split(", ")[:3])
    return names
