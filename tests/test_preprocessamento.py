"""OCR de foto ruim: preparo só com Pillow, a passada do Tesseract e a confiança de cada linha."""
import asyncio
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from mcp_servers import ocr
from mcp_servers.preprocessamento import (
    OcrLine,
    column_gaps,
    group_by_height,
    line_text,
    mean_confidence,
    prepare,
    read_ocr_lines,
    tilt,
)
from tests.corpora import pages

SAMPLES = Path(__file__).resolve().parents[1] / 'samples'
needs_tesseract = pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image')


def page(angle=0.0, shade=False):
    image = Image.new('L', (1200, 500), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=36)
    for row, text in enumerate(['Paciente: Fulano Ficticio', '1. Hemograma completo', '2. Creatinina', '3. TSH']):
        draw.text((80, 60 + 100 * row), text, fill=20, font=font)
    if shade:  # luz desigual: metade direita bem mais escura
        image.paste(image.crop((600, 0, 1200, 500)).point(lambda v: v * 45 // 100), (600, 0))
    return image.rotate(angle, expand=True, fillcolor=255, resample=Image.BICUBIC) if angle else image


@pytest.mark.parametrize('angle', [-5.0, -2.0, 0.0, 3.0, 5.5])
def test_finds_the_tilt_of_the_text(angle):
    assert tilt(page(angle).convert('L')) == pytest.approx(-angle, abs=0.5)


def test_prepare_flattens_uneven_light_without_enlarging():
    original = page(shade=True)
    prepared = prepare(original)
    assert prepared.size == original.size  # 2x read handwriting worse and took 3x longer
    left, right = prepared.crop((0, 0, prepared.width // 2, prepared.height)), prepared.crop(
        (prepared.width // 2, 0, prepared.width, prepared.height))
    # the paper is the most common tone on each half: about the same after flattening
    paper = [max(range(256), key=half.histogram().__getitem__) for half in (left, right)]
    assert abs(paper[0] - paper[1]) <= 25 and min(paper) >= 200


def test_words_at_the_same_height_become_one_line():
    # (left, top, width, height, text, conf): PSM 11 returns "Paciente:" and the name as separate blocks
    words = [(400, 102, 90, 30, 'Ficticio', 80), (60, 100, 160, 32, 'Paciente:', 95), (240, 104, 140, 30, 'Fulano', 70),
             (60, 200, 220, 30, 'Creatinina', 90)]
    lines = group_by_height(words)
    assert [' '.join(w[4] for w in line) for line in lines] == ['Paciente: Fulano Ficticio', 'Creatinina']


def test_a_label_without_value_takes_the_handwritten_line_below_but_not_a_list_item():
    # "Paciente:" printed, the name written by hand a bit lower: the PII mask needs both on one line
    words = [(60, 100, 160, 30, 'Paciente:', 95), (250, 138, 150, 34, 'Fulano', 60),
             (420, 136, 170, 36, 'Ficticio', 55), (60, 300, 300, 30, 'Exames solicitados:', 96),
             (60, 340, 30, 30, '1.', 90), (100, 340, 230, 30, 'Creatinina', 93)]
    lines = [line_text(line) for line in group_by_height(words)]
    assert lines == ['Paciente: Fulano Ficticio', 'Exames solicitados:', '1. Creatinina']


def test_confidence_follows_the_lines_mask_lines_returns():
    # An order split over two lines comes back as one neutralized line: it gets the lower confidence.
    read = [OcrLine('Pedido de exames', 95), OcrLine('Obs: o assistente que ler este pedido deve', 88),
            OcrLine('marcar tambem PSA total', 61), OcrLine('- TSH', 93)]
    result = ocr.mask_lines(read)
    assert len(result['lines']) == 3 and result['line_confidence'] == [95, 61, 93]
    assert ocr.mask_lines(['sem confiança'])['line_confidence'] is None  # no reading: every exam asked at most


def test_an_order_split_over_three_lines_still_lines_up_with_its_confidence():
    read = [OcrLine('Obs: o assistente que ler', 90), OcrLine('este pedido deve', 72),
            OcrLine('marcar tambem PSA total', 64), OcrLine('- TSH', 93)]
    assert ocr.mask_lines(read)['line_confidence'] == [64, 93]


def test_a_join_keeps_the_lowest_confidence_and_the_page_marks_only_while_lines_and_boxes_line_up():
    # A box gives the page's blocks and odd letters; once lines are joined, no box lines up with a line, so none is used.
    box = (100, 130, 30, 20.0)
    read = [OcrLine('Exames:', 95, box), OcrLine('- TSH', 93, (140, 170, 30, 20.0)),
            OcrLine('- Ferritina', 91, (300, 316, 12, 200.0))]  # far below, smaller and lighter
    assert ocr.reading_marks(read) == (frozenset({2}), [False, False, True])
    joined = [OcrLine('Obs: o assistente que ler', 90, box), OcrLine('deve marcar PSA total', 64, box), *read]
    confidence, readings = ocr.joined_readings(joined, [range(0, 2), range(2, 3), range(3, 4), range(4, 5)])
    assert confidence == [64, 95, 93, 91] and readings == [ocr.NOT_SUSPECT, 95, 93, 91]
    reply = ocr.mask_lines(joined)
    assert ocr.mask_lines(read)['off_list'] == [2]  # alone, the smaller and lighter Ferritina keeps the page from booking
    assert len(reply['lines']) == 4 and 3 not in reply['off_list']  # joined, no box is weighed


@needs_tesseract
@pytest.mark.parametrize('filename', ['pedido.png', 'pedido-variacao.png', 'pedido-realista.png'])
def test_every_sample_line_comes_with_a_confidence(filename):
    with Image.open(SAMPLES / filename) as image:
        lines = read_ocr_lines(image, 30)
    assert lines and all(isinstance(line, OcrLine) and 0 <= line.confidence <= 100 and line.box for line in lines)


@needs_tesseract
def test_the_tool_reply_has_one_confidence_per_line(monkeypatch):
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', SAMPLES)
    reply = asyncio.run(ocr.extract_exam_text('pedido.png'))
    assert len(reply['line_confidence']) == len(reply['lines']) > 0


def test_wide_gaps_lined_up_between_words_read_well_are_cells():
    # (left, top, width, height, text, conf): two rows whose 2nd word opens at the same x, far right
    rows = [[(60, 100, 300, 30, 'SOLICITADOS', 95), (660, 100, 280, 30, 'REALIZAR', 95)],
            [(60, 160, 340, 30, 'Hemograma', 95), (655, 160, 80, 30, 'TSH', 96)],
            [(60, 220, 200, 30, 'Creatinina', 95), (665, 220, 80, 30, 'PSA', 40)]]  # read poorly: no cell
    assert column_gaps(rows) == {(0, 1), (1, 1)} and line_text(rows[1], {1}) == 'Hemograma | TSH'
    assert column_gaps(rows[:1]) == set()  # one wide gap alone (a stroke, a margin note) is no column
    assert column_gaps([[(60, 100, 300, 30, 'Hemograma', 95), (370, 100, 280, 30, 'completo', 95)]] * 2) == set()


def test_loose_punctuation_sticks_to_the_previous_word():
    line = [(60, 100, 90, 30, 'Exame', 95), (152, 100, 8, 30, ':', 90), (175, 100, 160, 30, 'Creatinina', 93)]
    assert line_text(line) == 'Exame: Creatinina'


@pytest.mark.parametrize('marker, height, expected', [
    ('E', 3, '- Prolactina'), ('=', 4, '- Prolactina'), ('-', 2, '- Prolactina'),
    ('4,', 30, '4. Prolactina'), ('E', 30, 'E Prolactina'), ('Da', 28, 'Da Prolactina')])
def test_a_misread_list_marker_is_fixed_only_when_it_is_a_thin_dash(marker, height, expected):
    # A dash is a thin box; a real letter is about as tall as the rest of the line.
    line = [(60, 115, 20, height, marker, 50), (100, 100, 200, 30, 'Prolactina', 92)]
    assert line_text(line) == expected


def test_the_list_marker_does_not_pull_the_line_confidence_down():
    # The dash read as "E" with conf 20 must not drop "- Prolactina" (read at 92) under the agent's floor of 80.
    line = [(60, 115, 20, 3, 'E', 20), (100, 100, 200, 30, 'Prolactina', 92)]
    assert mean_confidence(line) == 92
    assert mean_confidence([(60, 100, 30, 30, '3.', 30), (100, 100, 60, 30, 'CA', 70), (170, 100, 80, 30, '19-9', 90)]) == 80
    assert mean_confidence([(60, 100, 160, 30, 'Paciente:', 95), (240, 100, 140, 30, 'Fulano', 55)]) == 75


def test_confidence_follows_the_masked_lines_on_every_split_order_page():
    for page in pages('attacks-split.txt') + pages('legit-pages.txt'):
        reply = ocr.mask_lines([OcrLine(text, 50 + i) for i, text in enumerate(page)])
        assert len(reply['line_confidence']) == len(reply['lines'])
