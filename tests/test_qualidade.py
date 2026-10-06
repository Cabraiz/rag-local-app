"""Photo quality check before the OCR: each problem gets its own reason; no shipped sample is refused."""
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image, ImageFilter

from mcp_servers import ocr
from mcp_servers.qualidade import quality_problem
from tests.load import pedidos

SAMPLES = Path(__file__).resolve().parents[1] / 'samples'


@pytest.fixture(scope='module')
def page(tmp_path_factory):
    """One fictional printed request from the load-test generator (fixed seed)."""
    folder = tmp_path_factory.mktemp('pedido')
    pedidos.gerar(folder, 1)
    return Image.open(folder / 'pedido-0001.png').convert('L')


def tiny(image):
    return image.resize((image.width * 250 // max(image.size), image.height * 250 // max(image.size)))


PROBLEMS = {
    'desfocada': (lambda image: image.filter(ImageFilter.GaussianBlur(12)), 'foto desfocada'),
    'escura': (lambda image: image.point(lambda v: v * 30 // 255), 'foto escura demais'),
    'sem contraste': (lambda image: image.point(lambda v: 255 - (255 - v) * 15 // 255), 'foto sem contraste'),
    'minúscula': (tiny, 'resolução baixa'),
}


def test_a_clean_page_is_accepted(page):
    assert quality_problem(page) is None


def test_a_header_strip_and_a_small_request_in_a_big_photo_are_accepted(page):
    strip = page.crop((0, 0, page.width, 150))  # wide and short, with text of normal size
    photo = Image.new('L', (4500, 4500), 255)    # under 1% of ink
    photo.paste(page, (1700, 1800))
    assert quality_problem(strip) is None and quality_problem(photo) is None


@pytest.mark.parametrize('problem', PROBLEMS)
def test_each_problem_gets_its_reason_with_a_tip(page, problem):
    damage, reason = PROBLEMS[problem]
    message = quality_problem(damage(page))
    assert message.startswith(reason) and 'tire outra' in message


@pytest.mark.parametrize('sample', sorted(p.name for p in SAMPLES.glob('*.png')))
def test_no_shipped_sample_is_refused(sample):
    with Image.open(SAMPLES / sample) as image:
        assert quality_problem(image) is None


def test_the_ocr_refuses_a_bad_photo_before_tesseract(page, tmp_path, monkeypatch):
    PROBLEMS['desfocada'][0](page).save(tmp_path / 'tremida.png')
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', tmp_path)
    monkeypatch.setattr(ocr, 'ler_linhas', lambda *a, **k: pytest.fail('Tesseract ran'))
    with pytest.raises(ToolError, match='^foto desfocada: segure o celular firme'):
        ocr.read_lines(ocr.resolve_sample('tremida.png'))
