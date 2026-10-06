# DGX OCR service

This optional service runs **on DGX**. The Mac application only renders PDF
pages, calls the service and saves page results. It ships no Paddle, RapidOCR,
ONNX Runtime, weights, SSH credentials or remote host account.

## Tested deployment

- PP-OCRv6 small official detection/recognition ONNX models, RapidOCR 3.9.2,
  ONNX Runtime 1.30.0, Python 3.12 on Linux aarch64.
- PP-DocLayoutV3 official ONNX model supplies regions and draft reading order.
  `layout.lock.json` and `experiments/ocr_v6/models.lock.json` pin six assets.
- CPU execution, four inference threads, one request at a time, a 6 GiB
  systemd memory cap. This deployment does not claim GPU acceleration.
- Service binds only `127.0.0.1:18086`; a private SSH forward connects the Mac.
  Bearer authentication is required for `/v1/ocr`. Health is `/health`.
- Request: `{ "model": "pp-ocrv6-small", "image": "<base64 PNG>" }`.
  Images are at most 2400 pixels per side, requests at most 12 MiB. Results
  use `whaleread-ocr-v1`; the client checks dimensions and every bounding box.
- No source names, page contents or tokens are written to service logs. Pages
  are handled in memory. There is no implicit model download at inference time.

## Installation and configuration

Create a dedicated venv under `~/.local/share/whaleread-ocr-v6` on DGX and
install `rapidocr==3.9.2 onnxruntime==1.30.0`. Copy `server.py`, `layout.lock.json`,
and the prototype's `engine.py` / `models.lock.json` into that directory.
Download the official archives named by the lock files, verify their SHA256
and byte sizes, extract them into `models`, then verify each model file hash.

Generate a random token locally on DGX in `secrets.env` (mode 0600), with
`WHALEREAD_OCR_TOKEN=<token>`. Install the provided systemd user service only
after all assets and the token exist; enable/start that new unit.

On the Mac, forward loopback port 18086 to DGX loopback port 18086 using the
existing authorized SSH connection. Keep the forwarding process running.
In WhaleRead's model settings, use **Add DGX OCR service**, save the private
service token to Keychain, select the profile for **PDF OCR**, and enable that
feature's sharing consent. A loopback endpoint may be an SSH tunnel, so OCR
requires consent even when the visible URL is local.

In **PDF · Research**, import a PDF. Single-page translation retains approval.
Enable **Batch translation · skip page approval** to recognize and translate
the document without approving each page. Source pages and paragraph results
are saved on the Mac. Pause/resume skips saved translations. Routes are bound
to the document, so resuming cannot silently change the OCR or translation
model. Draft translations are visible without marking pages reviewed.

Tables, detected figures and formulas remain image crops; original PDF pixels
remain available. Unassigned text is retained and flagged. A page with no
detections is preserved as an image and counted as needing review, never as
fully translated. Complex equations and reading order still require review.

## Rollback

Stop/disable **only** `spark-whaleread-ocr.service` and unload **only** the new
Mac OCR forwarding LaunchAgent. Keep the model directory and Mac research
jobs for recovery. The existing HY-MT2, PaddleOCR-VL and other DGX services
are separate and must not be stopped or reconfigured for this rollback.

## Upstream sources and licensing

- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR), Apache 2.0.
- [PaddleX](https://github.com/PaddlePaddle/PaddleX), Apache 2.0; official layout
  preprocessing and ordered bounding-box output define the adapter contract.
- [RapidOCR](https://github.com/RapidAI/RapidOCR), Apache 2.0.
- [ONNX Runtime](https://github.com/microsoft/onnxruntime), MIT.

The server adapter is project code. Full dependency/model licenses should
accompany any server redistribution; this service and its weights are not
part of the Mac application bundle.
