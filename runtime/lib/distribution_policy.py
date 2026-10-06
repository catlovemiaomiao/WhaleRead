"""Validate the sealed metadata for the free, GPL GitHub edition."""
from __future__ import annotations

import re


def validate_standalone(info, manifest):
    bundle_id = info.get('CFBundleIdentifier', '')
    if not re.fullmatch(r'[A-Za-z0-9]+(?:\.[A-Za-z0-9-]+)+', bundle_id):
        raise ValueError('Invalid application identity')
    if info.get('WhaleReadReceiptValidation') != 'standalone':
        raise ValueError('This is not a standalone application')
    if (manifest.get('schema_version') != 1 or manifest.get('mode') != 'standalone'
            or manifest.get('channel') != 'github'
            or manifest.get('bundle_identifier') != bundle_id
            or manifest.get('version') != info.get('CFBundleShortVersionString')
            or str(manifest.get('build')) != str(info.get('CFBundleVersion'))
            or manifest.get('license') != 'GPL-3.0-only'
            or manifest.get('purchase_required') is not False
            or not re.fullmatch(r'[a-f0-9]{64}', manifest.get('license_sha256', ''))):
        raise ValueError('Standalone distribution metadata does not match this application')
    return manifest
