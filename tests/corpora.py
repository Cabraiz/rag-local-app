"""Readers for the injection corpora in tests/attacks/, shared by the test files that use them."""
from pathlib import Path

ATTACKS_DIR = Path(__file__).resolve().parent / 'attacks'


def corpus(name):
    return (ATTACKS_DIR / name).read_text(encoding='utf-8').splitlines()


def pages(name):
    """Files of OCR pages: the lines of each page, pages separated by a '---' line."""
    found, page = [], []
    for line in corpus(name):
        if line == '---':
            found.append(page)
            page = []
        else:
            page.append(line)
    return found
