"""Small text helpers shared by ingestion and retrieval."""
import re

STOP = set("a an the is are was were be for of to in on at by and or with what which who when how did does do "
           "about that this it its as from into use used any there their our we you i can should".split())


def tokens(text):
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOP or len(w) < 2:
            continue
        out.append(w[:-1] if w.endswith("s") and len(w) > 3 else w)
    return out


def sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" -*\t") for p in parts if len(p.strip(" -*\t")) > 15]
