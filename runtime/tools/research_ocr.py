"""Run inside the isolated Paddle client environment, through a private SSH tunnel."""
import argparse
from pathlib import Path
from paddleocr import PaddleOCRVL

parser = argparse.ArgumentParser()
parser.add_argument('input')
parser.add_argument('output')
parser.add_argument('url')
args = parser.parse_args()
pipeline = PaddleOCRVL(pipeline_version='v1.6', device='cpu',
    vl_rec_backend='llama-cpp-server', vl_rec_server_url=args.url,
    vl_rec_api_model_name='PaddleOCR-VL-1.6-0.9B',
    use_doc_orientation_classify=False, use_doc_unwarping=False)
for result in pipeline.predict(input=args.input):
    result.save_to_json(save_path=Path(args.output))
