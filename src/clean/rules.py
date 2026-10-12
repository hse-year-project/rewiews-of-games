import re

import pandas as pd
from lingua import Language, LanguageDetectorBuilder

STEAM_LANGUAGE_CODES = {"english": "en", "russian": "ru", "schinese": "zh", "tchinese": "zh"}
DETECTABLE = [
    Language.from_str(name)
    for name in "english russian chinese german french spanish portuguese polish ukrainian turkish japanese korean".split()
]

HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")  # Chinese characters
REPEATS = re.compile(r"(.)\1{2,}")


def text(df):
    return df["review"].fillna("").str.strip()


def letter_share(s: str) -> float:
    chars = [c for c in s if not c.isspace()]
    return sum(c.isalpha() for c in chars) / len(chars) if chars else 1.0


def is_empty(df, cfg):
    """Too short to mean anything, except Chinese"""
    t = text(df)
    return (t.str.len() < cfg.min_chars) & ~t.str.contains(HAN)


def is_junk_text(s: str, min_letter_share: float) -> bool:
    collapsed = REPEATS.sub(r"\1", s)
    if letter_share(collapsed) < min_letter_share:
        return True
    if sum(c.isalpha() for c in collapsed) == 1 and not HAN.search(s):
        return True
    lines = [ln for ln in s.splitlines() if ln.strip()]
    art_lines = [ln for ln in lines if letter_share(ln) < min_letter_share]
    return len(lines) >= 5 and len(art_lines) > len(lines) / 2


def is_junk(df, cfg):
    return text(df).map(lambda s: is_junk_text(s, cfg.junk_min_letter_ratio)).astype(bool)


def is_duplicate_text(df, cfg):
    """Same author, same text more than once."""
    normalized = text(df).str.lower().str.split().str.join(" ")
    by_date = df.assign(norm=normalized).sort_values("created_at")
    duplicate = by_date.duplicated(["author_steamid", "norm"]).reindex(df.index)
    return duplicate & (normalized.str.len() >= cfg.dup_min_chars) & df["author_steamid"].notna()


def detect_language(df, cfg):
    """Adds lang_detected and lang_conf; empty for texts too short to judge."""
    df = df.assign(lang_detected=None, lang_conf=float("nan"))
    long_enough = text(df).str.len() >= cfg.lang_min_chars
    if long_enough.any():
        detector = LanguageDetectorBuilder.from_languages(*DETECTABLE).build()
        results = detector.compute_language_confidence_values_in_parallel(
            text(df)[long_enough].tolist()
        )
        df.loc[long_enough, "lang_detected"] = [
            r[0].language.iso_code_639_1.name.lower() for r in results
        ]
        df.loc[long_enough, "lang_conf"] = [r[0].value for r in results]
    return df


def text_language(df, cfg):
    """Language the text is written in."""
    declared = df["language"].map(STEAM_LANGUAGE_CODES)
    sure = df["lang_detected"].notna() & (df["lang_conf"] >= cfg.lang_min_confidence)
    t = text(df)
    mostly_chinese = t.str.count(HAN) >= t.map(lambda s: sum(c.isalpha() for c in s)) / 2
    detected = df["lang_detected"].mask(mostly_chinese & t.str.contains(HAN), "zh")
    return detected.where(sure, declared)


def is_lang_mismatch(df, cfg):
    """Text written in another language than the reviewer's Steam language."""
    declared = df["language"].map(STEAM_LANGUAGE_CODES)
    return declared.notna() & df["text_language"].notna() & (df["text_language"] != declared)


def is_edited(df, cfg):
    return df["updated_at"] - df["created_at"] > pd.Timedelta(hours=cfg.edited_min_delay_hours)


# Drop rules set keep = False; flag rules only mark the review.
DROP_RULES = [is_empty, is_junk, is_duplicate_text]
FLAG_RULES = [is_lang_mismatch, is_edited]
RULE_NAMES = [rule.__name__ for rule in DROP_RULES + FLAG_RULES]
FLAG_COLUMNS = [
    "recommendation_id",
    *RULE_NAMES,
    "lang_detected",
    "lang_conf",
    "text_language",
    "keep",
]


def add_flags(df, cfg):
    """One True/False column per rule, plus `text_language` and `keep`."""
    df = detect_language(df, cfg)
    df["text_language"] = text_language(df, cfg)
    for rule in DROP_RULES + FLAG_RULES:
        df[rule.__name__] = rule(df, cfg)
    df["keep"] = ~df[[rule.__name__ for rule in DROP_RULES]].any(axis=1)
    return df
