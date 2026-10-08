"""OCR MCP server: file boundary, PII masking before return and a real Tesseract run."""
import asyncio
import shutil
import unicodedata
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from guardrails import pii
from mcp_servers import ocr

SAMPLES = Path(__file__).resolve().parents[1] / 'samples'


@pytest.fixture(autouse=True)
def samples_dir(monkeypatch):
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', SAMPLES)


@pytest.mark.parametrize('filename, message', [
    ('', 'filename deve ser'),
    ('a' * 300 + '.png', 'longo demais'),
    ('/etc/passwd', 'sem pastas'),
    ('C:\\pedido.png', 'sem pastas'),
    ('C:pedido.png', 'sem pastas'),
    ('../pedido.png', 'sem pastas'),
    ('..\\pedido.png', 'sem pastas'),
    ('sub/pedido.png', 'sem pastas'),
    ('..', 'Extensão'),
    ('pedido.txt', 'Extensão'),
    ('ausente.png', 'não encontrado'),
])
def test_rejects_paths_outside_samples(filename, message):
    with pytest.raises(ToolError, match=message):
        ocr.resolve_sample(filename)


def test_rejects_large_file(monkeypatch):
    monkeypatch.setattr(ocr, 'MAX_FILE_BYTES', 1000)
    with pytest.raises(ToolError, match='grande demais'):
        ocr.resolve_sample('pedido.png')


def test_rejects_image_with_too_many_pixels(monkeypatch):
    monkeypatch.setattr(ocr, 'MAX_PIXELS', 1000)
    with pytest.raises(ToolError, match='resolução'):
        ocr.read_lines(ocr.resolve_sample('pedido.png'))


def test_rejects_file_that_is_not_an_image(tmp_path, monkeypatch):
    (tmp_path / 'falso.png').write_bytes(b'not an image')
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', tmp_path)
    with pytest.raises(ToolError, match='imagem válida'):
        ocr.read_lines(ocr.resolve_sample('falso.png'))


def test_rejects_truncated_image_with_a_clear_message(tmp_path, monkeypatch):
    (tmp_path / 'cortado.png').write_bytes((SAMPLES / 'pedido.png').read_bytes()[:20000])
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', tmp_path)
    with pytest.raises(ToolError, match='corrompida ou incompleta'):
        ocr.read_lines(ocr.resolve_sample('cortado.png'))


def test_rejects_content_that_does_not_match_the_extension(tmp_path, monkeypatch):
    from PIL import Image
    Image.new('RGB', (200, 100), 'white').save(tmp_path / 'falso.png', format='GIF')
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', tmp_path)
    with pytest.raises(ToolError, match='não corresponde à extensão'):
        ocr.read_lines(ocr.resolve_sample('falso.png'))


def no_tesseract(*args, **kwargs):
    raise AssertionError('Tesseract ran for a file the checks should have refused, or for check_image')


def test_check_image_accepts_a_sample_without_running_the_ocr(monkeypatch):
    from PIL import Image
    monkeypatch.setattr(ocr, 'ler_linhas', no_tesseract)
    with Image.open(SAMPLES / 'pedido.png') as image:
        width, height = image.size
    assert asyncio.run(ocr.check_image('pedido.png')) == {'format': 'PNG', 'width': width, 'height': height}


def write_gif(path):
    from PIL import Image
    Image.new('RGB', (200, 100), 'white').save(path, format='GIF')


def write_blank_page(path):
    from PIL import Image
    Image.new('RGB', (1200, 1600), 'white').save(path)


# A file each tool refuses before Tesseract; None: no file of that name.
BAD_FILES = {
    'ausente.png': None,
    '../pedido.png': None,
    'falso.png': lambda path: path.write_bytes(b'not an image'),
    'documento.png': lambda path: path.write_bytes(b'%PDF-1.4\n%fictional\n'),
    'gif.png': write_gif,
    'cortado.png': lambda path: path.write_bytes((SAMPLES / 'pedido.png').read_bytes()[:20000]),
    'em-branco.png': write_blank_page,
}


@pytest.mark.parametrize('filename', BAD_FILES)
def test_check_image_refuses_what_the_reading_refuses_with_the_same_message(tmp_path, monkeypatch, filename):
    # `cli run` asks check_image before the first model turn: its refusal must be the one the agent would get.
    if BAD_FILES[filename]:
        BAD_FILES[filename](tmp_path / filename)
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', tmp_path)
    monkeypatch.setattr(ocr, 'ler_linhas', no_tesseract)
    with pytest.raises(ToolError) as checked:
        asyncio.run(ocr.check_image(filename))
    with pytest.raises(ToolError) as read:
        asyncio.run(ocr.extract_exam_text(filename))
    assert str(checked.value) == str(read.value)


@pytest.mark.parametrize('limit, message', [('MAX_FILE_BYTES', 'grande demais'), ('MAX_PIXELS', 'resolução')])
def test_check_image_keeps_the_size_limits(monkeypatch, limit, message):
    monkeypatch.setattr(ocr, limit, 1000)
    with pytest.raises(ToolError, match=message):
        asyncio.run(ocr.check_image('pedido.png'))


def test_every_line_is_masked_and_counted(monkeypatch):
    monkeypatch.setattr(pii, 'mask', lambda line: ('[CPF]', {'CPF': 1}) if 'CPF' in line else (line, {}))
    result = ocr.mask_lines(['CPF: 1', 'Hemograma completo', 'CPF: 2'])
    assert result == {'lines': ['[CPF]', 'Hemograma completo', '[CPF]'], 'pii_masked': {'CPF': 2},
                      'line_intent': ['request', 'request', 'request'], 'contested_exams': [],
                      'instructions_removed': 0, 'text_removed': 0, 'cancel_unlinked': False, 'page_clean': True,
                      'exam_lines': [1]}


needs_tesseract = pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image')


# Real Tesseract + real guardrails.pii on each sample: exams stay, personal data is gone.
# Tesseract reads the e-mail "@" of pedido.png as "Qg"; the mask must still hide it.
SAMPLE_CASES = {
    'pedido.png': (['Hemograma completo', 'Glicemia de jejum', 'Creatinina'],
                   ['123.456.789', '90000-1234', 'sentinela', 'example', 'qrs']),
    'pedido-variacao.png': (['Hemograma completo', 'Glicose', 'Creatinina'],
                            ['123.456.789', '3333-4444', 'sentinela', '05/10/2026', '123456', 'qrs']),
    'pedido-realista.png': (['Convênio', 'CID', 'Obs', 'Hemoglobina glicada', 'Colesterol total', 'TSH'],
                            ['joao', 'silva', 'lima', '987.654.321', '98888-7777', 'e11.9', '01/10/2026',
                             '654321', '52 anos', 'ouro', 'diabetes']),
}


@needs_tesseract
@pytest.mark.parametrize('filename', SAMPLE_CASES)
def test_real_ocr_keeps_exams_and_masks_personal_data(filename):
    expected, secrets = SAMPLE_CASES[filename]
    result = ocr.mask_lines(ocr.read_lines(ocr.resolve_sample(filename)))
    text = '\n'.join(result['lines'])
    for exam_or_label in expected:
        assert exam_or_label in text
    for secret in secrets:
        assert secret not in text.lower(), (secret, result['lines'])
    assert result['pii_masked'].get('CPF') == 1
    assert 'TEXTO_REMOVIDO' not in result['pii_masked']  # counted apart, as text_removed: it is not PII


@needs_tesseract
def test_the_readme_sample_masks_the_same_personal_data():
    # docs/como-rodar.md and the videos show this count for pedido.png.
    result = ocr.mask_lines(ocr.read_lines(ocr.resolve_sample('pedido.png')))
    assert result['pii_masked'] == {'NOME': 2, 'CPF': 1, 'EMAIL': 1, 'TELEFONE': 1}
    assert result['lines'][2:] == ['Paciente: [NOME]', 'CPF: [CPF]', 'Email: [EMAIL]', 'Telefone: [TELEFONE]',
                                   'Medico: [NOME]', 'Exame: Hemograma completo', 'Exame: Glicemia de jejum',
                                   'Exame: Creatinina']



def test_the_order_without_exams_is_the_one_its_generator_draws():
    # Case (b) of docs/evidencias/log-alucinacao.txt: the image is in samples/ and its generator redraws it.
    from exemplos.gerar_pedido_sem_exame import desenhar
    from tests.load import pedidos
    if not Path(pedidos.FONTS[0]).is_file():
        pytest.skip('the DejaVu fonts of the Docker image draw it')
    from PIL import Image
    drawn, _ = desenhar()
    with Image.open(SAMPLES / 'pedido-sem-exame.png') as saved:
        assert (saved.mode, saved.size) == (drawn.mode, drawn.size) and saved.tobytes() == drawn.tobytes()


@needs_tesseract
def test_the_order_without_exams_masks_what_the_evidence_log_shows_and_has_no_exam():
    result = ocr.mask_lines(ocr.read_lines(ocr.resolve_sample('pedido-sem-exame.png')))
    log = (SAMPLES.parent / 'docs' / 'evidencias' / 'log-alucinacao.txt').read_text(encoding='utf-8')
    case = log.split('== (b) pedido-sem-exame.png', 1)[1].split('\n== ', 1)[0]
    masked = ', '.join(f'{kind} x{count}' for kind, count in result['pii_masked'].items())
    assert f'PII mascarada pelo OCR: {masked}' in case
    assert result['lines'][-3:] == ['Exames solicitados:', 'Dr. [NOME] - [CRM]', 'Data: [DATA]']

# Images that reach the OCR in an unusual shape (bugs 1 to 3 of the 5d0ccc1 bug hunt).
PEDIDO_EXAMS = ['Hemograma completo', 'Glicemia de jejum', 'Creatinina']
PHOTO = SAMPLES / 'fotos-celular' / 'foto-01.jpg'
PHOTO_EXAMS = ['Ferritina', 'PSA total', 'Proteinas totais', 'Alfa fetoproteina']


def read_saved(tmp_path, monkeypatch, image, name='pedido.png', **save):
    image.save(tmp_path / name, **save)
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', tmp_path)
    return '\n'.join(ocr.mask_lines(ocr.read_lines(ocr.resolve_sample(name)))['lines'])


def exams_read(text, exams):
    """The exams whose name is in the text, without accents or case."""
    def plain(value):
        return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))
    return [exam for exam in exams if plain(exam) in plain(text)]


@needs_tesseract
@pytest.mark.parametrize('angle', [0, 90, 180, 270])
@pytest.mark.parametrize('source, exams', [(SAMPLES / 'pedido.png', PEDIDO_EXAMS), (PHOTO, PHOTO_EXAMS)],
                         ids=['pedido', 'foto-celular'])
def test_a_page_turned_in_its_pixels_is_read_upright(tmp_path, monkeypatch, source, exams, angle):
    # No EXIF: the pixels themselves are turned, as in a sideways scan or an app that dropped the tag.
    from PIL import Image
    with Image.open(source) as image:
        turned = image.convert('RGB').rotate(angle, expand=True)
    name = 'girada' + source.suffix
    assert exams_read(read_saved(tmp_path, monkeypatch, turned, name), exams) == exams


@needs_tesseract
def test_a_phone_photo_with_exif_orientation_is_read_upright(tmp_path, monkeypatch):
    from PIL import Image
    with Image.open(PHOTO) as image:
        exif = image.getexif()
        exif[0x0112] = 6  # Orientation: the camera was turned; the viewer turns it back 90 degrees
        stored = image.convert('RGB').rotate(90, expand=True)
    text = read_saved(tmp_path, monkeypatch, stored, 'exif.jpg', exif=exif.tobytes())
    assert exams_read(text, PHOTO_EXAMS) == PHOTO_EXAMS


def test_a_page_the_osd_sees_turned_but_nobody_can_read_is_refused(monkeypatch):
    from PIL import Image

    from mcp_servers import preprocessamento
    monkeypatch.setattr(preprocessamento, 'palavras', lambda *args: [(0, 0, 10, 10, 'x', 20)])
    monkeypatch.setattr(preprocessamento, 'rotacao', lambda *args: 90)
    with pytest.raises(preprocessamento.ImagemGirada, match='de lado ou de cabeça para baixo'):
        preprocessamento.ler_linhas(Image.new('L', (800, 600), 255), 30)


def test_an_upright_poor_reading_stays_as_it_is_when_the_osd_does_not_see_a_turn(monkeypatch):
    from PIL import Image

    from mcp_servers import preprocessamento
    monkeypatch.setattr(preprocessamento, 'palavras', lambda *args: [(0, 0, 10, 10, 'TSH', 40)])
    monkeypatch.setattr(preprocessamento, 'rotacao', lambda *args: 0)
    assert preprocessamento.ler_linhas(Image.new('L', (800, 600), 255), 30) == ['TSH']


@needs_tesseract
@pytest.mark.parametrize('mode', ['RGBA', 'LA'])
def test_a_png_with_a_transparent_background_is_read_on_white(tmp_path, monkeypatch, mode):
    from PIL import Image, ImageOps
    with Image.open(SAMPLES / 'pedido.png') as image:
        page = image.convert('RGB')
    ink = ImageOps.invert(page.convert('L'))  # opaque text, transparent paper (black underneath)
    transparent = Image.new(mode, page.size, 0)
    transparent.putalpha(ink)
    assert exams_read(read_saved(tmp_path, monkeypatch, transparent), PEDIDO_EXAMS) == PEDIDO_EXAMS


def test_an_image_without_transparency_is_passed_through_untouched():
    from PIL import Image

    from mcp_servers.preprocessamento import sobre_branco
    image = Image.new('RGB', (10, 10), 'gray')
    assert sobre_branco(image) is image


def test_a_pdf_renamed_to_png_says_it_is_a_pdf(tmp_path, monkeypatch):
    from PIL import Image
    Image.new('RGB', (200, 100), 'white').save(tmp_path / 'pedido.png', format='PDF')
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', tmp_path)
    with pytest.raises(ToolError, match='é um PDF, não uma imagem: exporte a página como PNG ou JPEG'):
        ocr.read_lines(ocr.resolve_sample('pedido.png'))
