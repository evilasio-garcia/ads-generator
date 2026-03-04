import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _candidate_files():
    files = []
    files.extend(ROOT.glob("*.py"))
    files.extend((ROOT / "scripts").glob("**/*.py"))
    files.extend((ROOT / "static").glob("**/*.html"))
    return [p for p in files if p.is_file()]


MOJIBAKE_PATTERNS = {
    # Typical mojibake from UTF-8 bytes interpreted as CP1252/Latin-1.
    "c3_mojibake_seq": re.compile(r"\u00C3[\u0080-\u00BF]"),
    "c2_mojibake_seq": re.compile(r"\u00C2[\u0080-\u00BF]"),
    "e2_cp1252_seq": re.compile(
        r"\u00E2(?:"
        r"\u20AC|\u201A|\u0192|\u201E|\u2026|\u2020|\u2021|\u02C6|\u2030|\u0160|\u2039|\u0152|"
        r"\u2018|\u2019|\u201C|\u201D|\u2022|\u2013|\u2014|\u02DC|\u2122|\u0161|\u203A|\u0153|\u0178"
        r")"
    ),
    "replacement_char": re.compile(r"\uFFFD"),
    "c1_control_chars": re.compile(r"[\u0080-\u009F]"),
}


def _line_hits(text: str):
    hits = []
    for i, line in enumerate(text.splitlines(), start=1):
        found = [name for name, pattern in MOJIBAKE_PATTERNS.items() if pattern.search(line)]
        if found:
            hits.append((i, found, line))
    return hits


def test_text_files_are_utf8_without_bom_and_without_mojibake():
    failures = []

    for path in _candidate_files():
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            failures.append(f"{path}: UTF-8 BOM detectado (esperado sem BOM).")

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            failures.append(f"{path}: arquivo nao e UTF-8 valido ({exc}).")
            continue

        for line_no, pattern_names, line in _line_hits(text):
            snippet = line.encode("unicode_escape").decode("ascii")
            failures.append(
                f"{path}:{line_no}: suspeita de mojibake ({','.join(pattern_names)}) -> {snippet}"
            )

    assert not failures, "Problemas de encoding/mojibake encontrados:\n" + "\n".join(failures)
