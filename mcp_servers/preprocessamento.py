"""Bad photos and handwriting: preparation with Pillow only, and one Tesseract pass with its confidence (no LLM: the
raw image never leaves the OCR process). 1. Grayscale, uneven light flattened, contrast stretched, straightened by up
to 6 degrees; no upscaling (at 2x Tesseract read handwriting worse, 3x slower). 2. One pass in PSM 11 (sparse text),
which finds the loose words of a handwritten order; the words form lines again by height, and a label without a value
("Paciente:") takes the line below, so the PII mask sees both. On 185 orders, exams found in the text went from 45% to
53% (handwritten 23% -> 34%) at 1.6x the latency. Each line read is an OcrLine.
"""
import re
from dataclasses import dataclass
from statistics import fmean, median

import pytesseract
from PIL import Image, ImageChops, ImageFilter, ImageOps

LANG = 'por'
CONFIG = '--oem 1 --psm 11'
MAX_ANGLE, COARSE_STEP, FINE_STEP = 6.0, 1.0, 0.2
GLUED_PUNCTUATION = set(':;,.)!?')
LIST_ITEM = re.compile(r'\s*(?:\d+\s*[.)]|[-–•*])')  # the agent searches each item in the catalog
DASH_HEIGHT = 0.35  # a dash measured 6% to 26% of the letters' height; a lower-case letter, 50% or more
NUMBER_WITH_COMMA = re.compile(r'\d{1,2},')
CONFIDENT = 70  # Tesseract's conf from which a word counts as read well
POOR_WORDS, POOR_FRACTION = 8, 0.5  # pedido.png upright: 32 of 35; on its side: 0; upside down: 7 of 33
MIN_OSD = 2.0  # the OSD's orientation_conf: from 2.5 (a doctor's hand) to 15 (a printed photo) in the tests
ROTATED_GAIN = 2  # the turned reading must read twice the confident words (and at least POOR_WORDS)
OVERLAP = 0.5  # the share of the smaller height (word or line) the two must have in common
COLUMN_GAP = 4  # a gap between two words, in letter heights, that separates columns (words: 0.3 to 1.3)

# (left, top, width, height, text, conf) of a word, as Tesseract reads it.
Word = tuple[int, int, int, int, str, float]
# (top, bottom, height of the tallest word, ink: the median of each word's darkest tone, 0 black to 255).
Box = tuple[int, int, int, float]


@dataclass(frozen=True)
class OcrLine:
    """A line read: its text, Tesseract's mean confidence in its words (0-100) and where it is on the page.
    A line from anywhere else (a page of text in a test) has neither: each reader of it says what that means."""
    text: str
    confidence: float | None = None
    box: Box | None = None


def prepare(image):
    """The image in grayscale, with the light flattened, the contrast stretched, and straight."""
    gray = flatten_light(ImageOps.exif_transpose(image).convert('L'))
    gray = ImageOps.autocontrast(gray, cutoff=1)
    angle = tilt(gray)
    if angle:
        gray = gray.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    return gray


def flatten_light(gray):
    """Removes shadow and uneven light: the background (paper) is the local maximum on a reduced copy."""
    small = gray.resize((max(1, gray.width // 8), max(1, gray.height // 8)), Image.Resampling.BOX)
    paper = small.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.BoxBlur(2)).resize(gray.size, Image.Resampling.BILINEAR)
    return ImageChops.invert(ImageChops.subtract(paper, gray))  # 255 - (paper - pixel): evenly white paper


def tilt(gray):
    """The angle (degrees) that makes the text lines horizontal: the one of the highest projection variance."""
    scale = 600 / max(gray.size)
    small = ImageOps.invert(gray.resize((max(1, round(gray.width * scale)), max(1, round(gray.height * scale)))))

    def sharpness(angle):
        turned = small.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=0)
        rows = list(turned.resize((1, turned.height), Image.Resampling.BOX).tobytes())  # mode L: one byte per row
        mean = fmean(rows)
        return fmean((value - mean) ** 2 for value in rows)

    steps = round(MAX_ANGLE / COARSE_STEP)
    best = max((sharpness(a * COARSE_STEP), a * COARSE_STEP) for a in range(-steps, steps + 1))[1]
    best = max((sharpness(a), a) for a in [best + k * FINE_STEP for k in range(-4, 5)])[1]
    return round(best, 1) if abs(best) >= 0.3 else 0.0


def read_words(image, timeout, lang=LANG, config=CONFIG) -> list[Word]:
    """Each word Tesseract read (a conf below 0: no word)."""
    data = pytesseract.image_to_data(image, lang=lang, config=config, output_type=pytesseract.Output.DICT, timeout=timeout)
    return [(data['left'][i], data['top'][i], data['width'][i], data['height'][i], text.strip(), float(data['conf'][i]))
            for i, text in enumerate(data['text']) if text.strip() and float(data['conf'][i]) >= 0]


def group_by_height(read):
    """Groups into lines the words that share at least half their height with a line already open (its band grows with
    each word: handwriting goes up and down); then a label without a value takes the line right below it."""
    lines: list[dict] = []
    for word in sorted(read, key=lambda w: w[1] + w[3] / 2):
        top, bottom = word[1], word[1] + word[3]
        for line in lines:
            common = min(line['bottom'], bottom) - max(line['top'], top)
            if common >= OVERLAP * min(word[3], line['bottom'] - line['top']):
                line['words'].append(word)
                line['top'], line['bottom'] = min(line['top'], top), max(line['bottom'], bottom)
                break
        else:
            lines.append({'top': top, 'bottom': bottom, 'words': [word]})
    grouped: list[dict] = []
    for line in sorted(lines, key=lambda item: item['top']):
        line['words'].sort()  # left to right
        if grouped and takes_the_line_below(grouped[-1], line):
            grouped[-1]['words'] += line['words']  # the label, then the line below, in that order
            grouped[-1]['bottom'] = max(grouped[-1]['bottom'], line['bottom'])
        else:
            grouped.append(line)
    return [line['words'] for line in grouped]


def takes_the_line_below(label: dict, line: dict) -> bool:
    """A label without a value ("Paciente:") right above a line that is not a list item ("1. Hemograma" stays alone)."""
    return (line_text(label['words']).endswith(':') and not LIST_ITEM.match(line_text(line['words']))
            and line['top'] - label['bottom'] <= label['bottom'] - label['top'])


def fix_marker(line):
    """The line with its list marker fixed: PSM 11 reads the dash as "E", "=" or "�" (a box much lower than the
    letters) and "4." as "4,"; without its marker, a line no longer looks like an item of the order."""
    if len(line) >= 2:
        first, height = line[0], median(w[3] for w in line[1:])
        if len(first[4]) <= 2 and first[3] < DASH_HEIGHT * height:
            return [(*first[:4], '-', first[5]), *line[1:]]
        if NUMBER_WITH_COMMA.fullmatch(first[4]):
            return [(*first[:4], first[4][:-1] + '.', first[5]), *line[1:]]
    return line


def line_text(line, cuts=()):
    """The line's text, with its marker fixed, the punctuation PSM 11 reads loose (":", ",") glued back to the
    word before it, and a "|" before each word in `cuts` (column_gaps())."""
    text = ''
    for index, (*_, word, _conf) in enumerate(fix_marker(line)):
        text += word if text and all(c in GLUED_PUNCTUATION for c in word) else f' {"| " * (index in cuts)}{word}'
    return text.strip()


def column_gaps(lines):
    """{(line, word)} of each column gap: COLUMN_GAP letter heights or more between two words read well, opening
    where another line's opens (1.5 heights). Measured: none on the 159 images of samples/."""
    gaps = [(i, j, b[0], median(w[3] for w in line)) for i, line in enumerate(lines)
            for j, (a, b) in enumerate(zip(line, line[1:], strict=False), 1)
            if b[0] - a[0] - a[2] >= COLUMN_GAP * median(w[3] for w in line) and min(a[5], b[5]) >= CONFIDENT]
    return {(i, j) for i, j, x, h in gaps if any(abs(x - x2) <= 1.5 * max(h, h2) for i2, _, x2, h2 in gaps if i2 != i)}


def mean_confidence(line):
    """Tesseract's mean conf over the words, the list marker aside: the dash or the "3." almost always
    comes with a low conf, and it dropped below 80 an exam printed and read with certainty."""
    line = fix_marker(line)
    if len(line) >= 2 and LIST_ITEM.fullmatch(line[0][4]):
        line = line[1:]
    return fmean(w[5] for w in line)


class SidewaysImage(ValueError):
    """The page is on its side or upside down and could not be read even after turning it."""


def on_white(image):
    """An image with transparency over white paper: gray drops the alpha, and a transparent background (black
    underneath) would read as a black page. An image without alpha comes back as it is."""
    if image.mode not in ('RGBA', 'LA', 'PA') and 'transparency' not in image.info:
        return image
    image = ImageOps.exif_transpose(image)  # compositing loses the EXIF: apply the orientation first
    return Image.alpha_composite(Image.new('RGBA', image.size, 'white'), image.convert('RGBA')).convert('RGB')


def confident(read):
    """How many words Tesseract read with conf >= CONFIDENT: the score of a reading."""
    return sum(w[5] >= CONFIDENT for w in read)


def poor_reading(read):
    """Few confident words, or few of those read: an empty page, on its side, or upside down."""
    return confident(read) < POOR_WORDS or confident(read) < POOR_FRACTION * len(read)


def rotation(image, timeout):
    """Degrees (90, 180, 270) the Tesseract OSD says to turn clockwise, or 0 if it cannot tell."""
    try:
        osd = pytesseract.image_to_osd(image, config='--psm 0', output_type=pytesseract.Output.DICT,
                                       timeout=timeout)
    except RuntimeError:  # too little text to decide, or out of time: the upright reading stays
        return 0
    return osd['rotate'] if osd['orientation_conf'] >= MIN_OSD else 0


def read_ocr_lines(image, timeout, prepared=True, lang=LANG, config=CONFIG) -> list[OcrLine]:
    """The non-empty lines, top to bottom, each with its confidence and box. When the reading is poor, the OSD says
    whether the page is turned; the turned reading wins only if it reads ROTATED_GAIN times the confident words, and if
    neither will do, SidewaysImage: better to ask for another photo than to return nothing without saying why."""
    image = prepare(image) if prepared else ImageOps.exif_transpose(image).convert('L')
    read = read_words(image, timeout, lang, config)
    if poor_reading(read) and (degrees := rotation(image, timeout)):
        turned_image = image.rotate(-degrees, expand=True, fillcolor=255)
        turned = read_words(turned_image, timeout, lang, config)
        if confident(turned) >= max(POOR_WORDS, ROTATED_GAIN * confident(read)):
            read, image = turned, turned_image
        elif confident(read) < POOR_WORDS:
            raise SidewaysImage('imagem de lado ou de cabeça para baixo: gire e envie de novo')
    lines = group_by_height(read)
    cuts = column_gaps(lines)  # a table or columns: each cell separated by "|"
    return [OcrLine(line_text(line, {j for k, j in cuts if k == i}), round(float(mean_confidence(line)), 1),
                    box_of(line, image)) for i, line in enumerate(lines)]


def box_of(line, image) -> Box:
    """(top, bottom, height, ink) of a line: where it is, the size of its letters and how dark they are."""
    tones = [image.crop((w[0], w[1], w[0] + w[2], w[1] + w[3])).getextrema()[0] for w in line]
    return min(w[1] for w in line), max(w[1] + w[3] for w in line), max(w[3] for w in line), median(tones)
