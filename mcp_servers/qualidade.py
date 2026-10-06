"""Photo quality check that runs before the OCR: a photo it would barely read is refused with a tip.

Pillow only, on the grayscale image:
- resolution: the longer side, in pixels (a header strip is wide and short, and still legible);
- light: how bright the paper is (90th percentile of the pixels, 0 to 255);
- contrast: paper minus ink (the darkest 0.2% of the pixels, at most 2000 of them: in a big
  photo of a small request, the ink is a tiny fraction of the image);
- focus: a photo is called blurred only when two measures agree, both taken after a 3x3 median
  that removes sensor noise and divided by the contrast, so a dark but sharp photo is not blurred:
  the variance of the Laplacian with the page at 550 px (all its edges), and the strongest
  edges (the top 0.2% of the Laplacian, at most 2000 pixels) at 550 or 2200 px, which still
  see the sharp text of a page with little ink, such as a small request in a big photo.
Each limit sits where the OCR (with the preparation of preprocessamento.py) stops reading
load-test requests degraded step by step, with sensor noise and JPEG; it was checked on the 120
simulated handwritten requests: no scan, load-test request or file in samples/ is refused
(samples/ in tests/test_qualidade.py).
"""
from PIL import Image, ImageFilter, ImageStat

MIN_LONG_SIDE = 320   # at 300 px, 1% of the exams read; at 350 px, 30%
MIN_PAPER = 44        # as measured on noisy photos: paper at 40, nothing read; at 48, 16%
MIN_CONTRAST = 28     # contrast 22 to 27: nothing read; 26 to 32: 7%; 30 to 37: 38%
MIN_EDGES = 10        # printed page blurred by 6 px: 5.5 at most; a handwritten photo the OCR still reads: 13+
MIN_STRONGEST = 3.0   # same pages: 2.8 at most from 6 px on; a handwritten photo the OCR still reads: 3.3+
REFERENCE_CONTRAST = 150  # a printed request on white paper
INK_PIXELS = 2000     # ink and strongest-edge samples: 0.2% of a 1-megapixel page
PAGE_SIDE = 550       # the edges are measured with the longer side at this size, whatever the camera


def percentile(histogram: list[int], fraction: float) -> int:
    """Level below which `fraction` of the counted values fall."""
    target, seen = fraction * sum(histogram), 0
    for level, count in enumerate(histogram):
        seen += count
        if seen >= target:
            return level
    return len(histogram) - 1


def resized(gray: Image.Image, long_side: int, enlarge: bool) -> Image.Image:
    scale = long_side / max(gray.size)
    if scale >= 1 and not enlarge:
        return gray
    size = (max(1, round(gray.width * scale)), max(1, round(gray.height * scale)))
    return gray.resize(size, Image.Resampling.BOX if scale < 1 else Image.Resampling.BICUBIC)


def laplacian(gray: Image.Image, divisor: int) -> Image.Image:
    """Laplacian of the denoised image, centered on 128 and without the border, which Pillow leaves unfiltered."""
    kernel = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=divisor, offset=128)
    edges = gray.filter(ImageFilter.MedianFilter(3)).filter(kernel)
    return edges.crop((1, 1, edges.width - 1, edges.height - 1))


def strongest_edge(gray: Image.Image) -> int:
    """Laplacian magnitude (divided by 8 so that it never clips) of the top 0.2%, at most 2000 pixels."""
    magnitude = [0] * 129
    for level, count in enumerate(laplacian(gray, 8).histogram()):
        magnitude[min(128, abs(level - 128))] += count
    return percentile(magnitude, 1 - min(0.002, INK_PIXELS / sum(magnitude)))


def measure(image: Image.Image) -> dict:
    """long_side, paper, contrast, edges and strongest: the measures quality_problem compares."""
    gray = image.convert('L')
    histogram = gray.histogram()
    paper = percentile(histogram, 0.90)
    contrast = paper - percentile(histogram, min(0.002, INK_PIXELS / sum(histogram)))
    relative = max(contrast, 1) / REFERENCE_CONTRAST
    edges = ImageStat.Stat(laplacian(resized(gray, PAGE_SIDE, enlarge=True), 1)).var[0] / relative ** 1.5
    strongest = max(strongest_edge(resized(gray, side, enlarge=False)) for side in (PAGE_SIDE, 4 * PAGE_SIDE))
    return {'long_side': max(gray.size), 'paper': paper, 'contrast': contrast,
            'edges': round(edges, 1), 'strongest': round(strongest / relative, 1)}


def quality_problem(image: Image.Image) -> str | None:
    """None if the photo can be read; otherwise the reason, with what to do."""
    m = measure(image)
    if m['long_side'] < MIN_LONG_SIDE:
        return 'resolução baixa: aproxime o celular do pedido e tire outra foto'
    if m['paper'] < MIN_PAPER:
        return 'foto escura demais: tire outra com mais luz'
    if m['contrast'] < MIN_CONTRAST:
        return 'foto sem contraste: o texto quase não se separa do papel; tire outra com mais luz e sem reflexo'
    if m['edges'] < MIN_EDGES and m['strongest'] < MIN_STRONGEST:
        return 'foto desfocada: segure o celular firme, espere focar e tire outra'
    return None
