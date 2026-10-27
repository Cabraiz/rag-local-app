"""The README and docs/ stay honest: links, paths, compose commands and the numbers they quote.

Offline and fast (a few seconds): every check reads the repository, nothing calls Docker or the
network. The one exception is the collected test count (`pytest --collect-only`, about 7 s), in
its own test at the end. A failure names the document, the line and what is stale; fix the text
(or, when the text is right and the rule here too strict, the rule).
"""
import ast
import json
import re
import shlex
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / 'README.md', *sorted((ROOT / 'docs').glob('*.md'))]
# Quoted on purpose although not in the repository, with the reason.
NOT_IN_REPO = {
    'docker-compose.override.yml': 'the file the reader creates when Docker has no free subnet',
    'specs/minha-spec.json': "the reader's own spec, in the folder the agent service mounts",
}
GENERATED = 'generated/'  # what `cli transpile` writes goes to the "generated" Docker volume, not the repository


def rel(path):
    return path.relative_to(ROOT).as_posix()


@cache
def lines_of(path):
    """(line number, text) of a Markdown file, with a flag for lines inside a ``` block."""
    out, fence = [], None
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        marker = re.match(r'\s*(```+)\s*(\w*)', line)
        if marker and fence is None:
            fence = marker.group(2) or 'text'
            continue
        if marker and fence is not None and not marker.group(2):
            fence = None
            continue
        out.append((number, line, fence))
    return out


# --- 1. Links and anchors -------------------------------------------------------------------------

LINK = re.compile(r'\[[^\]]*\]\(([^)\s]+)\)')


def slug(heading):
    """GitHub's anchor for a heading: lower case, punctuation out (accents stay), spaces to hyphens."""
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', heading)  # a link in a heading keeps its text
    text = re.sub(r'[^\w\- ]', '', text.replace('`', '').strip().lower())
    return text.replace(' ', '-')


@cache
def anchors(path):
    """Every anchor a Markdown file offers: its headings (with GitHub's -1, -2 for repeats) and <a id>."""
    found, seen = set(), {}
    for _, line, fence in lines_of(path):
        if fence is None and (heading := re.match(r'#{1,6}\s+(.*)', line)):
            base = slug(heading.group(1))
            count = seen.get(base, 0)
            seen[base] = count + 1
            found.add(base if count == 0 else f'{base}-{count}')
        found.update(re.findall(r'<a\s+(?:id|name)="([^"]+)"', line))
    return found


def link_problem(doc, target):
    """None if a relative link resolves; otherwise what is wrong with it."""
    path_part, _, anchor = target.partition('#')
    path = (doc.parent / path_part).resolve() if path_part else doc
    if not path.exists():
        return f'{path_part} does not exist'
    if anchor and path.suffix == '.md' and anchor not in anchors(path):
        return f'anchor #{anchor} is not a heading or <a id> of {rel(path)}'
    return None


def test_every_relative_link_and_anchor_resolves():
    stale = []
    for doc in DOCS:
        for number, line, fence in lines_of(doc):
            for target in LINK.findall(line) if fence is None else []:
                if re.match(r'[a-z]+:', target):  # https:, mailto:
                    continue
                if problem := link_problem(doc, target):
                    stale.append(f'{rel(doc)}:{number}: link ({target}): {problem}')
    assert not stale, '\n'.join(stale)


# --- 2. Paths quoted in backticks -----------------------------------------------------------------

PATH_LIKE = re.compile(r'[\w.-]+(?:/[\w.-]+)*/?')
FILE_SUFFIXES = {'.py', '.json', '.jsonl', '.yml', '.yaml', '.md', '.txt', '.png', '.jpg', '.toml', '.ini', '.mp4'}


def looks_like_repo_path(token):
    """A relative path whose first folder is in the repository, or that names a project file."""
    if not PATH_LIKE.fullmatch(token) or '/' not in token or token.startswith(('.', '/')):
        return False
    return (ROOT / token.split('/')[0]).exists() or Path(token.rstrip('/')).suffix in FILE_SUFFIXES


def quoted_paths(doc):
    """(line number, path) for every inline `code` and every word of a shell block that looks like a path."""
    for number, line, fence in lines_of(doc):
        if fence is None:
            tokens = re.findall(r'`([^`]+)`', line)
        elif fence in ('bash', 'sh', 'shell', 'powershell'):
            tokens = line.split()
        else:
            continue
        for token in tokens:
            if looks_like_repo_path(token):
                yield number, token


def test_every_quoted_repository_path_exists():
    stale = []
    for doc in DOCS:
        for number, path in quoted_paths(doc):
            if path in NOT_IN_REPO or path.startswith(GENERATED):
                continue
            if (ROOT / path).exists() or (doc.parent / path).exists():
                continue
            stale.append(f'{rel(doc)}:{number}: `{path}` does not exist in the repository')
    assert not stale, '\n'.join(stale)



TEST_ID = re.compile(r'(test_\w+\.py)::(?:Test\w+::)?(test_\w+)')


def test_every_quoted_test_id_exists():
    """`test_x.py::test_y` in the docs names a test that pytest would find: a reader pastes it into pytest."""
    files = {path.name: path for path in (ROOT / 'tests').rglob('test_*.py')}
    stale, found = [], 0
    for doc in DOCS:
        for number, line, _ in lines_of(doc):
            for match in TEST_ID.finditer(line):
                found += 1
                file, name = match.groups()
                source = files[file].read_text(encoding='utf-8') if file in files else ''
                if not re.search(rf'^\s*(?:async )?def {name}\(', source, re.M):
                    stale.append(f'{rel(doc)}:{number}: `{match.group(0)}` is not a test in tests/')
    assert found, 'no `file::test` id found in the docs'
    assert not stale, '\n'.join(stale)


def test_every_test_file_is_in_the_developer_guide_map():
    guide = (ROOT / 'docs' / 'guia-dev.md').read_text(encoding='utf-8')
    table = guide.split('Onde cada tema é testado', 1)[1].split('\n\n- ', 1)[0]
    missing = [rel(path) for path in sorted((ROOT / 'tests').rglob('test_*.py')) if path.name not in table]
    assert not missing, 'missing from the table "Onde cada tema é testado" of docs/guia-dev.md: ' + ', '.join(missing)


def test_every_message_the_report_writes_is_in_the_warnings_table():
    """Each fixed piece of every message runtime/relatorio.py writes about an exam is in the table a reader searches."""
    from runtime import relatorio
    reference = (ROOT / 'docs' / 'referencia.md').read_text(encoding='utf-8')
    table = reference.split('### Avisos sobre os exames de um pedido', 1)[1].split('\n### ', 1)[0]
    messages = [*relatorio.WHY.values(), *relatorio.LEFT_OUT.values(), *relatorio.REFUSED.values()]
    pieces = {piece.strip() for message in messages for piece in re.split(r'\{\w+\}', message) if len(piece.strip()) > 8}
    missing = sorted(piece for piece in pieces if piece not in table)
    assert not missing, 'missing from "Avisos sobre os exames de um pedido" in docs/referencia.md: ' + ' | '.join(missing)


# --- 2b. Prose a reader can scan ------------------------------------------------------------------

LIST_ITEM = re.compile(r'\s*(?:[-*]|\d+\.) ')
MAX_WORDS, MAX_ASIDES = 35, 1  # per sentence; an aside is a parenthesis of 4 words or more
MAX_CELL_WORDS, MAX_CELL_SENTENCES = 40, 2  # per table cell, sentences or clauses: the detail goes below the table
SENTENCE_END = re.compile(r'(?<=[.!?])\s+(?=[A-Z"*(])')
CLAUSE_END = re.compile(r'(?<=[.!?;])\s+')  # in a cell, a semicolon starts another idea too


def plain_text(text):
    """The words a reader reads: a link keeps its text, inline code counts as one word."""
    return re.sub(r'`[^`]*`', 'X', re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text))


def prose_paragraphs(doc):
    """(first line, text) of each paragraph or list item outside code, tables, headings and the one-line indexes."""
    out, current, start = [], [], 0
    for number, line, fence in lines_of(doc) + [(0, '', None)]:
        stripped = line.strip()
        skip = fence or stripped.startswith(('|', '#', '<', 'https://')) or ' · ' in stripped or stripped.endswith(' ·')
        if skip or not stripped or LIST_ITEM.match(line):
            if current:
                out.append((start, ' '.join(current)))
            current, start = ([stripped] if stripped and not skip else []), number
            continue
        current, start = current + [stripped], start if current else number
    return out


def test_prose_sentences_stay_short():
    """One idea per sentence, the rule before the exception (the Google developer documentation style)."""
    stale = []
    for doc in DOCS:
        for number, text in prose_paragraphs(doc):
            for sentence in SENTENCE_END.split(plain_text(text)):
                words = len(sentence.split())
                asides = sum(len(inner.split()) >= 4 for inner in re.findall(r'\(([^()]*)\)', sentence))
                if words > MAX_WORDS or asides > MAX_ASIDES:
                    stale.append(f'{rel(doc)}:{number}: {words} words, {asides} asides: {sentence[:80]}…')
    assert not stale, 'split these sentences:\n' + '\n'.join(stale)


def test_a_list_item_never_runs_into_an_unindented_line():
    """A line right after a list item, not indented and not a new item, is glued to the item on GitHub."""
    stale = []
    for doc in DOCS:
        previous = (0, '', None)
        for number, line, fence in lines_of(doc):
            if not fence and not previous[2] and LIST_ITEM.match(previous[1]) and line.strip() \
                    and not line.startswith((' ', '|', '#', '<', '`')) and not LIST_ITEM.match(line):
                stale.append(f'{rel(doc)}:{number}: {line[:60]}')
            previous = (number, line, fence)
    assert not stale, 'indent the line or leave a blank line before it:\n' + '\n'.join(stale)


def test_two_lists_never_render_as_one():
    """A blank line between two lists of the same kind and indent does not end the first: GitHub shows one list."""
    stale = []
    for doc in DOCS:
        rows = lines_of(doc)
        for at, (number, line, fence) in enumerate(rows):
            if fence or not LIST_ITEM.match(line):
                continue
            after = next((row for row in rows[at + 1:] if row[1].strip()), None)
            if after is None or after[0] == number + 1 or after[2] or not LIST_ITEM.match(after[1]):
                continue
            indent = len(line) - len(line.lstrip())
            numbered = line.lstrip()[0].isdigit()
            if len(after[1]) - len(after[1].lstrip()) == indent and after[1].lstrip()[0].isdigit() == numbered:
                stale.append(f'{rel(doc)}:{after[0]}: {after[1].strip()[:60]}')
    assert not stale, 'put a sentence between the two lists, or join them:\n' + '\n'.join(stale)


def test_table_cells_hold_one_idea():
    """A cell is a short answer; conditions, exceptions and examples go in a note below the table."""
    stale = []
    for doc in DOCS:
        for number, line, fence in lines_of(doc):
            row = line.strip()
            if fence or not row.startswith('|') or set(row) <= set('|-: '):
                continue
            for cell in re.split(r'(?<!\\)\|', row.strip('|')):
                text = plain_text(cell).strip()
                words, sentences = len(text.split()), len([part for part in CLAUSE_END.split(text) if part])
                if words > MAX_CELL_WORDS or sentences > MAX_CELL_SENTENCES:
                    stale.append(f'{rel(doc)}:{number}: {words} words, {sentences} sentences: {text[:70]}…')
    assert not stale, 'move the detail of these cells below the table:\n' + '\n'.join(stale)


WE = re.compile(r'\b(?:nós|nosso|nossa|nossos|nossas|[a-zç]+amos)\b', re.IGNORECASE)
WE_WORDS = {'ramos', 'gamos', 'tamos'}  # whole words that end like a verb, not first person


def test_the_docs_speak_in_one_voice():
    """The README and docs/ are impersonal; only revisao.md, the author's account of the work, says "I"."""
    stale = [f'{rel(doc)}:{number}: {match.group(0)}' for doc in DOCS if doc.name != 'revisao.md'
             for number, line, fence in lines_of(doc) if not fence
             for match in WE.finditer(re.sub(r'<[^>]*>', '', plain_text(line))) if match.group(0).lower() not in WE_WORDS]
    assert not stale, 'write these impersonally:\n' + '\n'.join(stale)


# --- 3. Numbers the docs quote that can be computed offline --------------------------------------

def read_json(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def text_lines(path):
    return (ROOT / path).read_text(encoding='utf-8').splitlines()


@cache
def facts():
    """The numbers, computed from the repository itself."""
    catalog = read_json('data/exams.json')
    legit = text_lines('tests/attacks/legit.txt')
    legit_pages = [line for line in text_lines('tests/attacks/legit-pages.txt') if line != '---']
    queries = [json.loads(line) for line in text_lines('tests/calibration/queries.jsonl')]
    handwritten = read_json('samples/manuscritos/gabarito.json')['imagens'].values()
    photos = read_json('samples/fotos-celular/gabarito.json')['imagens'].values()
    pii_types = len({kind for kind, _ in pii_patterns()}) + 1  # + the names, masked by their own rule
    return {
        'catalog': len(catalog),
        'catalog_terms': len({term for exam in catalog for term in (exam['name'], *exam.get('synonyms', []))}),
        'pii_types_besides_4': pii_types - 4,  # "nome, CPF, telefone, e-mail e mais N tipos"
        'pii_cases': pii_corpus_size(),
        'attacks': len(text_lines('tests/attacks/attacks.txt')),
        'legit': len(legit),
        'legit_distinct': len(set(legit)),
        'legit_all_distinct': len(set(legit) | set(legit_pages)),
        'queries': len(queries),
        'queries_ocr': sum(query['source'] == 'ocr' for query in queries),
        'queries_synthetic': sum(query['source'] != 'ocr' for query in queries),
        'handwritten': len(handwritten),
        'handwritten_common': sum(item['estilo'] == 'comum' for item in handwritten),
        'handwritten_doctor': sum(item['estilo'] == 'medico' for item in handwritten),
        'handwritten_photo': sum(item['degradacao'] != 'scan' for item in handwritten),
        'handwritten_photo_pct': 100 * sum(item['degradacao'] != 'scan' for item in handwritten) / len(handwritten),
        'photos': len(photos),
        'photo_exams': sum(len(item['exames']) for item in photos),
        'test_functions': count_test_functions(),
    }


def pii_patterns():
    from guardrails.pii_rules import PATTERNS
    return PATTERNS


def pii_corpus_size():
    from tests.pii_corpus import GENERATORS, PER_KIND
    return {'kinds': len(GENERATORS), 'per_kind': PER_KIND, 'total': len(GENERATORS) * PER_KIND}


def count_test_functions():
    """test_* functions (and Test* methods) in the files pytest collects, as `pytest --collect-only` names them."""
    total = 0
    for path in (ROOT / 'tests').rglob('test_*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        bodies = [tree.body] + [node.body for node in tree.body if isinstance(node, ast.ClassDef)
                                and node.name.startswith('Test')]
        total += sum(isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith('test')
                     for body in bodies for node in body)
    return total


def number(text):
    """'1.380' -> 1380, '16,7' -> 16.7 (the docs write numbers the Brazilian way)."""
    text = text.replace('.', '')
    return float(text.replace(',', '.')) if ',' in text else int(text)


# (documents, pattern with named groups, {group: fact}). Every pattern must still be found,
# so a reworded sentence fails here instead of silently going unchecked.
CLAIMS = [
    ('README.md', r'(?P<n>\d+) exames fictícios', {'n': 'catalog'}),
    ('README.md', r'`data/exams.json`\]\([^)]*\) \((?P<n>\d+) exames\)', {'n': 'catalog'}),
    ('README.md', r'<br/>(?P<n>\d+) exames', {'n': 'catalog'}),
    ('README.md', r'e-mail e mais (?P<n>\d+) tipos', {'n': 'pii_types_besides_4'}),
    ('*', r'(?P<n>\d+) funções(?: de teste)?', {'n': 'test_functions'}),
    ('*', r'(?P<n>[\d.]+) ataques', {'n': 'attacks'}),
    ('*', r'(?P<n>[\d.]+) linhas legítimas,? \(?(?P<d>[\d.]+) distintas', {'n': 'legit', 'd': 'legit_distinct'}),
    ('*', r'(?P<n>[\d.]+) linhas legítimas distintas \(as (?P<d>[\d.]+) distintas de `tests/attacks/legit.txt`',
     {'n': 'legit_all_distinct', 'd': 'legit_distinct'}),
    ('*', r'(?P<n>[\d.]+) linhas legítimas e os (?P<t>\d+) termos', {'n': 'legit_all_distinct', 't': 'catalog_terms'}),
    ('*', r'(?P<t>\d+) (?:nomes e sinônimos|termos no catálogo)', {'t': 'catalog_terms'}),
    ('*', r'(?P<n>[\d.]+) consultas(?: versionadas| de calibração|;)', {'n': 'queries'}),
    ('*', r'(?P<o>\d+) linhas de OCR de \d+ pedidos fictícios, já mascaradas, e (?P<s>\d+) consultas sintéticas',
     {'o': 'queries_ocr', 's': 'queries_synthetic'}),
    ('*', r'(?P<n>[\d.]+) casos gerados (?:de PII|por `tests/pii_corpus.py`)', {'n': ('pii_cases', 'total')}),
    ('*', r'(?P<k>\d+) tipos × (?P<p>\d+)', {'k': ('pii_cases', 'kinds'), 'p': ('pii_cases', 'per_kind')}),
    ('*', r'tem (?P<n>\d+) pedidos fictícios com aparência de letra de mão: (?P<c>\d+) de letra comum e (?P<m>\d+)',
     {'n': 'handwritten', 'c': 'handwritten_common', 'm': 'handwritten_doctor'}),
    ('*', r'(?P<f>\d+) das (?P<n>\d+) \((?P<p>\d+)%\) imitam foto',
     {'f': 'handwritten_photo', 'n': 'handwritten', 'p': 'handwritten_photo_pct'}),
    ('*', r'(?P<n>\d+) fotos de celular', {'n': 'photos'}),
    ('*', r'(?P<n>\d+) fotos, (?P<e>\d+) exames', {'n': 'photos', 'e': 'photo_exams'}),
]


def agrees(quoted, actual):
    """A count must be exact; a percentage (a float here) may be rounded either way: 77,5% is 77% or 78%."""
    return abs(quoted - actual) < 1 if isinstance(actual, float) else quoted == actual


def fact(key):
    value = facts()
    for part in key if isinstance(key, tuple) else (key,):
        value = value[part]
    return value


def claim_lines(documents):
    for doc in DOCS if documents == '*' else [ROOT / documents]:
        for number_, line, _ in lines_of(doc):
            yield doc, number_, line


@pytest.mark.parametrize('documents, pattern, groups', CLAIMS, ids=[claim[1][:50] for claim in CLAIMS])
def test_numbers_quoted_in_the_docs_match_the_repository(documents, pattern, groups):
    found, stale = 0, []
    for doc, line_number, line in claim_lines(documents):
        for match in re.finditer(pattern, line):
            found += 1
            for group, key in groups.items():
                if not agrees(number(match.group(group)), fact(key)):
                    stale.append(f'{rel(doc)}:{line_number}: "{match.group(0)}" says {match.group(group)}, '
                                 f'the repository has {fact(key)} ({key})')
    assert found, f'no sentence matches {pattern!r} any more: update the claim in this test'
    assert not stale, '\n'.join(stale)


# --- Sample output and the API contract the docs quote --------------------------------------------

def text_blocks(doc):
    """The ```text blocks of a Markdown file, as lists of non-empty lines."""
    blocks, current = [], None
    for _, line, fence in lines_of(doc) + [(0, '', None)]:
        if fence == 'text':
            current = (current or []) + ([line] if line.strip() else [])
        elif current is not None:
            blocks.append(current)
            current = None
    return blocks


def test_the_sample_transpile_line_matches_the_spec():
    spec = read_json('specs/agent.json')
    steps = ' -> '.join(agent['name'] for agent in spec['agents'])
    expected = re.compile(rf'OK: generated/agent\.py gerado e importado; root_agent "{spec["name"]}" '
                          rf'\(SequentialAgent: {re.escape(steps)}\)')
    quoted = [(doc, line) for doc in DOCS for block in text_blocks(doc) for line in block if line.startswith('OK: ')]
    assert quoted, 'no sample "OK: ..." transpile line found'
    stale = [f'{rel(doc)}: {line!r} does not match the spec ({spec["name"]}: {steps})'
             for doc, line in quoted if not expected.fullmatch(line)]
    assert not stale, '\n'.join(stale)


def test_the_sample_exam_table_matches_the_catalog():
    names = {exam['code']: exam['name'] for exam in read_json('data/exams.json')}
    rows = [(doc, row) for doc in DOCS for block in text_blocks(doc) for line in block
            if (row := re.fullmatch(r'\|\s*(.+?)\s*\|\s*(FICT-\d{3})\s*\|', line))]
    assert rows, 'no sample exam table found'
    stale = [f'{rel(doc)}: "{row.group(1)}" is {row.group(2)}, but the catalog calls {row.group(2)} '
             f'"{names.get(row.group(2))}"' for doc, row in rows if names.get(row.group(2)) != row.group(1)]
    assert not stale, '\n'.join(stale)


def in_log(line, log):
    """True if the log has this line; a '…' in the docs stands for the rest of a value (a shortened id)."""
    pattern = re.compile(r'\S*'.join(re.escape(part) for part in line.strip().split('…')))
    return any(pattern.fullmatch(entry.strip()) for entry in log)


def test_the_sample_run_output_is_the_one_in_the_evidence_log():
    log = (ROOT / 'docs' / 'evidencias' / 'log-run-pedido.txt').read_text(encoding='utf-8').splitlines()
    blocks = [(doc, block) for doc in DOCS for block in text_blocks(doc)
              if any(line.startswith('Tempo:') for line in block)]
    assert blocks, 'no sample run output (a block with "Tempo:") found'
    stale = [f'{rel(doc)}: {line!r} is not in docs/evidencias/log-run-pedido.txt'
             for doc, block in blocks for line in block if not line.startswith('OK: ') and not in_log(line, log)]
    assert not stale, '\n'.join(stale)


def test_the_api_operations_the_docs_name_exist():
    routes = set(re.findall(r"@app\.(get|post)\('([^']+)', operation_id='(\w+)'",
                            (ROOT / 'api' / 'main.py').read_text(encoding='utf-8')))
    guide = (ROOT / 'docs' / 'como-rodar.md').read_text(encoding='utf-8')
    quoted = re.findall(r'`(\w+)` \(`(GET|POST) ([^`]+)`\)', guide)
    assert quoted, 'no "`operation` (`METHOD /path`)" found in docs/como-rodar.md'
    stale = [f'docs/como-rodar.md: {operation} ({method} {path}) is not an operation of api/main.py'
             for operation, method, path in quoted if (method.lower(), path, operation) not in routes]
    assert not stale, '\n'.join(stale)


# --- 4. Compose commands --------------------------------------------------------------------------

@cache
def compose(files=('docker-compose.yml',)):
    """(services, profiles) of the given compose files merged, read with a small indentation parser."""
    services, profiles = set(), set()
    for name in files:
        section = None
        for line in (ROOT / name).read_text(encoding='utf-8').splitlines():
            if re.match(r'\S', line):
                section = line.split(':')[0]
            elif section == 'services' and (service := re.match(r' {2}([\w-]+):', line)):
                services.add(service.group(1))
            if listed := re.search(r'profiles:\s*\[([^\]]*)\]', line):
                profiles.update(item.strip(' "\'') for item in listed.group(1).split(','))
    return services, profiles


def compose_commands(doc):
    """(line number, words) of every `docker compose` command: shell blocks and inline `code`."""
    for number_, line, fence in lines_of(doc):
        candidates = [line] if fence in ('bash', 'sh', 'shell', 'powershell') else (
            re.findall(r'`(docker compose [^`]+)`', line) if fence is None else [])
        for command in candidates:
            if command.strip().startswith('docker compose'):
                yield number_, shlex.split(command, comments=True)


GLOBAL_VALUED = {'-f', '--file', '-p', '--project-name', '--profile', '--env-file'}
RUN_VALUED = {'-e', '--env', '--name', '-v', '--volume', '-w', '--workdir', '--entrypoint', '-u', '--user', '-p',
              '--publish'}
SERVICE_ARGS = {'run', 'exec', 'port', 'logs', 'up', 'build', 'ps', 'down'}


def split_options(words, valued):
    """({option: [values]}, the other words): the options in `valued` take the next word as their value."""
    options, rest, index = {}, [], 0
    while index < len(words):
        word = words[index]
        if word in valued and index + 1 < len(words):
            options.setdefault(word, []).append(words[index + 1])
            index += 2
            continue
        if not word.startswith('-'):
            rest.append(word)
        index += 1
    return options, rest


def command_problems(words):
    """What does not exist among the compose files, profiles, services and modules a command names."""
    global_words = words[2:]
    subcommand_at = next((i for i, word in enumerate(global_words) if word in SERVICE_ARGS), None)
    options, _ = split_options(global_words[:subcommand_at], GLOBAL_VALUED)
    files = tuple(options.get('-f', [])) or ('docker-compose.yml',)
    problems = [f'compose file {name} does not exist' for name in files
                if name not in NOT_IN_REPO and not (ROOT / name).exists()]
    services, profiles = compose(tuple(name for name in files if (ROOT / name).exists()))
    problems += [f'profile {name} is not in docker-compose.yml' for name in options.get('--profile', [])
                 if name not in profiles]
    if subcommand_at is None:
        return problems
    subcommand, after = global_words[subcommand_at], global_words[subcommand_at + 1:]
    if subcommand in ('run', 'exec'):  # run [options] <service> <command...>
        _, args = split_options(after, RUN_VALUED)
        named = args[:1]
        command = after[after.index(named[0]) + 1:] if named else []
    else:  # logs -f ocr rag api, port api 8000, up/down with flags: their words are services or numbers
        named = [word for word in after if not word.startswith('-') and not word.isdigit()]
        command = []
    problems += [f'service {name} is not in {", ".join(files)}' for name in named if name not in services]
    return problems + module_problems(command)


def module_problems(command):
    """The `python -m <module>` a container command runs must be a file of the repository."""
    if command[:2] != ['python', '-m'] or len(command) < 3:
        return []
    module = command[2]
    if any((ROOT / f'{module.replace(".", "/")}{suffix}').exists() for suffix in ('.py', '/__main__.py')):
        return []
    return [f'python module {module} does not exist']


def test_compose_commands_name_files_profiles_services_and_modules_that_exist():
    services, _ = compose()
    assert {'ocr', 'rag', 'api', 'agent'} <= services, f'docker-compose.yml services: {sorted(services)}'
    stale, seen = [], 0
    for doc in DOCS:
        for number_, words in compose_commands(doc):
            if any('<' in word or '$' in word for word in words):  # a placeholder: <arquivo>, $(...)
                continue
            seen += 1
            stale += [f'{rel(doc)}:{number_}: {" ".join(words)}: {problem}' for problem in command_problems(words)]
    assert seen >= 4, 'the README quick start commands were not found'
    assert not stale, '\n'.join(stale)


# --- The collected test count (slow: runs `pytest --collect-only`) ----------------------------------

def test_the_collected_test_count_the_docs_quote_is_current():
    out = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider'],
                         cwd=ROOT, capture_output=True, text=True, timeout=300).stdout
    collected = int(re.search(r'(\d+) tests? collected', out).group(1))
    claims = [(r'(?P<n>[\d.]+) casos \(`pytest --collect-only`\)', lambda n: n == collected),
              (r'(?P<n>[\d.]+) passam e 1 é pulado', lambda n: n == collected - 1),
              (r'\((?P<n>\d+,\d) mil casos', lambda n: abs(n - collected / 1000) < 0.1)]  # 16.765: "16,7 mil"
    stale, found = [], 0
    for doc, line_number, line in claim_lines('*'):
        for pattern, holds in claims:
            for match in re.finditer(pattern, line):
                found += 1
                if not holds(number(match.group('n'))):
                    stale.append(f'{rel(doc)}:{line_number}: "{match.group(0)}", but pytest collects {collected}')
    assert found, 'no collected-count claim found in the docs'
    assert not stale, '\n'.join(stale)
