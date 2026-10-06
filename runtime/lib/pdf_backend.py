"""PDFium presentation adapter. No source PDF is ever modified in place.

The small geometry/text surface keeps research layout and review semantics
independent from the renderer. PDFium uses BSD-style licenses.
"""
import ctypes
import math
from contextlib import closing
from pathlib import Path
import pypdfium2 as pdfium
import pypdfium2.raw as raw
from PIL import ImageDraw


class Rect:
    def __init__(self, *values):
        if len(values) == 1:
            value = values[0]
            values = (value.x0, value.y0, value.x1, value.y1) if isinstance(value, Rect) else tuple(value)
        self.x0, self.y0, self.x1, self.y1 = map(float, values)

    @property
    def width(self): return max(0, self.x1 - self.x0)
    @property
    def height(self): return max(0, self.y1 - self.y0)
    @property
    def is_empty(self): return not self.width or not self.height
    def get_area(self): return self.width * self.height
    def contains(self, other): return self.x0 <= other.x0 and self.y0 <= other.y0 and self.x1 >= other.x1 and self.y1 >= other.y1
    def __and__(self, other): return Rect(max(self.x0, other.x0), max(self.y0, other.y0), min(self.x1, other.x1), min(self.y1, other.y1))
    def __or__(self, other): return Rect(min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1))
    def __iter__(self): return iter((self.x0, self.y0, self.x1, self.y1))


class Matrix:
    def __init__(self, x, y): self.x, self.y = x, y


class Pixmap:
    def __init__(self, image): self.image = image
    def save(self, path): self.image.save(str(path))
    @property
    def samples(self): return self.image.tobytes()
    def pixel(self, x, y): return self.image.getpixel((x, y))


def color(obj, stroke=False):
    channels = [ctypes.c_uint() for _ in range(4)]
    fn = raw.FPDFPageObj_GetStrokeColor if stroke else raw.FPDFPageObj_GetFillColor
    if not fn(obj, *[ctypes.byref(c) for c in channels]): return None
    return tuple(c.value for c in channels[:3])


class Page:
    def __init__(self, page):
        self.page = page
        self.rect = Rect(0, 0, *page.get_size())
        self.redactions, self.masks = [], []

    def box(self, obj):
        left, bottom, right, top = obj.get_bounds()
        return Rect(left, self.rect.height - top, right, self.rect.height - bottom)

    def get_text(self, mode='text', clip=None):
        clip = Rect(clip) if clip is not None else self.rect
        with closing(self.page.get_textpage()) as textpage:
            if mode != 'dict':
                return textpage.get_text_bounded(clip.x0, self.rect.height - clip.y1, clip.x1, self.rect.height - clip.y0).replace('\r\n', '\n')
            blocks = []
            for obj in self.page.get_objects(textpage=textpage):
                box = self.box(obj)
                if (box & clip).is_empty: continue
                if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
                    blocks.append(dict(type=1, bbox=list(box)))
                elif obj.type == raw.FPDF_PAGEOBJ_TEXT:
                    text = obj.extract()
                    if not text.strip(): continue
                    font = obj.get_font().get_base_name()
                    rgb = color(obj) or (0, 0, 0)
                    span = dict(text=text, bbox=list(box), size=obj.get_font_size(), font=font,
                                flags=2 if any(k in font.lower() for k in ('italic', 'oblique')) else 0,
                                color=(rgb[0] << 16) + (rgb[1] << 8) + rgb[2])
                    blocks.append(dict(type=0, bbox=list(box), lines=[dict(spans=[span])]))
            return dict(blocks=blocks)

    def get_drawings(self):
        result = []
        for obj in self.page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]):
            rgb = color(obj, stroke=True)
            # Four-sided paths are the frame candidates used by the page layout.
            if raw.FPDFPath_CountSegments(obj) in (4, 5):
                result.append(dict(rect=self.box(obj), color=rgb, items=[('re',)]))
        return result

    def add_redact_annot(self, rect, **kwargs): self.redactions.append(Rect(rect))
    def draw_rect(self, rect, **kwargs): self.masks.append((Rect(rect), kwargs.get('fill', (1, 1, 1))))

    def apply_redactions(self, **kwargs):
        # Disposable presentation only: remove matched text objects, not images
        # or vector paths, and never persist this mutated document over the source.
        for obj in list(self.page.get_objects(filter=[raw.FPDF_PAGEOBJ_TEXT])):
            box = self.box(obj)
            if any((box & area).get_area() / max(.001, box.get_area()) > .55 for area in self.redactions):
                self.page.remove_obj(obj)
                obj.close()
        if self.redactions: self.page.gen_content()

    def get_pixmap(self, matrix=None, clip=None, alpha=False):
        scale = matrix.x if matrix else 1
        bitmap = self.page.render(scale=scale)
        image = bitmap.to_pil().convert('RGB')
        bitmap.close()
        draw = ImageDraw.Draw(image)
        for rect, fill in self.masks:
            draw.rectangle(tuple(v * scale for v in rect), fill=tuple(round(c * 255) for c in fill))
        if clip is not None:
            box = Rect(clip) & self.rect
            image = image.crop((math.floor(box.x0*scale), math.floor(box.y0*scale), math.ceil(box.x1*scale), math.ceil(box.y1*scale)))
        return Pixmap(image)


class Document:
    def __init__(self, path=None):
        self.document = pdfium.PdfDocument(str(path)) if path is not None else pdfium.PdfDocument.new()
        self.pages = {}
        self.needs_pass = False
    def __len__(self): return len(self.document)
    def __getitem__(self, index):
        if index not in self.pages: self.pages[index] = Page(self.document[index])
        return self.pages[index]
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def close(self):
        for page in self.pages.values(): page.page.close()
        self.pages.clear()
        self.document.close()
    def insert_pdf(self, source, from_page, to_page): self.document.import_pages(source.document, pages=list(range(from_page, to_page + 1)))
    def save(self, path): self.document.save(str(path))


open = Document
