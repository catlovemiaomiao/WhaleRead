"""Optional, CPU-only OCR prototype; deliberately outside the release runtime.

Uses the pinned RapidOCR 3.9.2 detector/recognizer modules, with official
PP-OCRv6 ONNX weights and preprocessing. No classifier, model download, server,
translation, or implicit replacement of an existing text layer.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import tempfile


def offline_only():
    """Fail any network attempt, including an upstream implicit model download."""
    def deny(event, args):
        if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo',
                     'socket.bind', 'socket.sendto'}:
            raise RuntimeError('OCR inference is offline; network access is disabled')
    sys.addaudithook(deny)


def verify_models(directory):
    directory = Path(directory).resolve()
    lock = json.loads(Path(__file__).with_name('models.lock.json').read_text())
    for relative, expected in lock['files'].items():
        file = directory / relative
        if not file.is_file():
            raise FileNotFoundError(f'Missing local OCR asset: {relative}')
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError(f'OCR model hash mismatch: {relative}')
    return directory


class LocalOCR:
    def __init__(self, models, threads=4):
        models = verify_models(models)
        for name, version in {'rapidocr':'3.9.2', 'onnxruntime':'1.30.0'}.items():
            if importlib.metadata.version(name) != version:
                raise RuntimeError(f'This prototype requires {name}=={version}')
        import rapidocr
        import yaml
        from rapidocr.ch_ppocr_det import TextDetector
        from rapidocr.ch_ppocr_rec import TextRecognizer
        from rapidocr.utils.parse_parameters import ParseParams
        cfg = ParseParams.load(Path(rapidocr.__file__).with_name('config.yaml'))
        det = models / 'PP-OCRv6_small_det_onnx_infer'
        rec = models / 'PP-OCRv6_small_rec_onnx_infer'
        cfg = ParseParams.update_batch(cfg, {
            'EngineConfig.onnxruntime.intra_op_num_threads':threads,
            'EngineConfig.onnxruntime.inter_op_num_threads':1,
            'Det.model_path':str(det / 'inference.onnx'),
            'Rec.model_path':str(rec / 'inference.onnx'),
            # Match the evaluated PaddleOCR pipeline, not RapidOCR defaults.
            'Det.limit_side_len':64, 'Det.limit_type':'min',
            'Det.mean':[0.485,0.456,0.406], 'Det.std':[0.229,0.224,0.225],
            'Det.thresh':0.3, 'Det.box_thresh':0.6,
            'Det.unclip_ratio':1.5, 'Det.use_dilation':False,
            'Det.max_candidates':1000, 'Rec.rec_batch_num':6,
        })
        # Supply the exact model dictionary even when ONNX metadata lacks it.
        # The temporary file is removed after the recognizer has loaded it.
        chars = yaml.safe_load((rec / 'inference.yml').read_text())['PostProcess']['character_dict']
        with tempfile.TemporaryDirectory(prefix='whaleread-ocr-dict-') as temp:
            keyfile = Path(temp) / 'characters.txt'
            keyfile.write_text('\n'.join(chars)+'\n', encoding='utf-8')
            cfg.Rec.rec_keys_path = str(keyfile)
            cfg.Det.engine_cfg = cfg.EngineConfig.onnxruntime
            cfg.Rec.engine_cfg = cfg.EngineConfig.onnxruntime
            cfg.Rec.font_path = None
            self.detector = TextDetector(cfg.Det)
            self.recognizer = TextRecognizer(cfg.Rec)

    def recognize(self, image):
        import numpy as np
        from rapidocr.ch_ppocr_rec import TextRecInput
        from rapidocr.utils.process_img import get_rotate_crop_image
        # Render callers cap the long side. Never change supplied page geometry.
        if max(image.size) > 2400:
            raise ValueError('OCR prototype accepts pages up to 2400 pixels per side')
        array = np.array(image.convert('RGB'))[:, :, ::-1].copy()
        detected = self.detector(array)
        if detected.boxes is None or not len(detected.boxes):
            return []
        crops = [get_rotate_crop_image(array, box.copy()) for box in detected.boxes]
        result = self.recognizer(TextRecInput(img=crops, return_word_box=False))
        if result.txts is None or len(result.txts) != len(detected.boxes):
            raise RuntimeError('OCR recognition count does not match detected lines')
        return [dict(text=text, confidence=float(confidence),
                     polygon=box.tolist(), needs_review=True,
                     low_confidence=bool(confidence < .8))
                for box, text, confidence in zip(detected.boxes, result.txts, result.scores)]
