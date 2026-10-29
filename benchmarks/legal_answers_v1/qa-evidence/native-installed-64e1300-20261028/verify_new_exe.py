"""Read-only: compare every app.* module bundled in dist/Lexono/Lexono.exe with the committed source (HEAD)."""
import marshal, subprocess, sys
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

EXE = r"<REPOSITORY>\dist\Lexono\Lexono.exe"
PYZ = r"<QA-ARBEITSORDNER>\exe_new_PYZ.pyz"
car = CArchiveReader(EXE)
open(PYZ, "wb").write(car.extract(next(n for n in car.toc if "PYZ" in n)))
pyz = ZlibArchiveReader(PYZ)


def sig(code):
    parts = [code.co_code, code.co_names]
    for c in code.co_consts:
        parts.append(sig(c) if hasattr(c, "co_code") else (repr(sorted(map(repr, c))) if isinstance(c, frozenset) else repr(c)))
    return tuple(parts)


def load(m):
    c = pyz.extract(m)
    c = c[1] if isinstance(c, tuple) else c
    return marshal.loads(c) if isinstance(c, bytes) else c


mods = sorted(n for n in pyz.toc if n == "app" or n.startswith("app."))
same, diff, missing = 0, [], []
for m in mods:
    path = m.replace(".", "/")
    src = None
    for p in (path + ".py", path + "/__init__.py"):
        r = subprocess.run(["git", "show", f"HEAD:{p}"], capture_output=True, text=True, encoding="utf-8")
        if r.returncode == 0:
            src, fn = r.stdout, p
            break
    if src is None:
        missing.append(m)
        continue
    if sig(load(m)) == sig(compile(src, fn, "exec")):
        same += 1
    else:
        diff.append(m)
print(f"app-Module im Bundle: {len(mods)}, identisch mit HEAD: {same}, abweichend: {diff}, ohne Quelle: {missing}")
need = {"app.privacy.presidio_ner": ["is_transliterated_common_noun", "is_contextual_technical_term", "cited_statute_abbreviations", "detect_name_column_labels"],
        "app.privacy.review": ["_feminine_article_name_reading", "_is_definite_article_name_reading"],
        "app.privacy.detectors": ["short_form_statute_collisions", "is_confidential_organisation_mention", "_organisation_short_forms"],
        "app.chat.service": []}
for m, names in need.items():
    code = load(m)
    have = {c.co_name for c in code.co_consts if hasattr(c, "co_code")}
    print(m, {n: n in have for n in names})
