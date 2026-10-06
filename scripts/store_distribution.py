"""Local Mac App Store identity/profile checks; Apple acceptance is separate.

Profile fields are inspected according to Apple's TN3125. They are not a public
file-format contract; reject unsupported profiles instead of guessing grants.
No private key is exported, and no developer-account session is opened.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import plistlib
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
QA_ID = re.compile(r'local\.sindy\.jingdu\.storeqa(?:[0-9]+|\.[A-Za-z0-9.-]+)')
TEAM_ID = re.compile(r'[A-Z0-9]{10}')
APP_IDENTITIES = ('Apple Distribution:', '3rd Party Mac Developer Application:')
INSTALLER_IDENTITIES = ('Mac Installer Distribution:', '3rd Party Mac Developer Installer:')


def run(command):
    result = subprocess.run(command, capture_output=True,
                            env={**os.environ, 'LC_ALL': 'C', 'LANG': 'C'})
    if result.returncode:
        raise ValueError(f'{Path(command[0]).name} failed: '
                         + result.stderr.decode('utf-8', errors='replace').strip()[:2000])
    return result


def validate_store_identity(bundle_id, team_id):
    if not isinstance(team_id, str) or not TEAM_ID.fullmatch(team_id):
        raise ValueError('An explicit 10-character Apple Team ID is required')
    if not isinstance(bundle_id, str) or not re.fullmatch(r'[A-Za-z0-9]+(?:\.[A-Za-z0-9-]+)+', bundle_id):
        raise ValueError('An explicit registered Bundle ID is required')
    if QA_ID.fullmatch(bundle_id) or bundle_id.startswith('local.sindy.jingdu.storecandidate'):
        raise ValueError('A QA or provisional candidate identity cannot be submitted')


def choose_identity(listing, requested, team_id, *, installer=False):
    """Match a valid private-key-backed identity by exact name or SHA-1 selector."""
    if not TEAM_ID.fullmatch(team_id or ''):
        raise ValueError('An explicit Apple Team ID is required')
    rows = re.findall(r'^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"([^"\r\n]+)"\s*$', listing, re.M)
    matches = [(digest.upper(), name) for digest, name in rows
               if requested == name or requested.upper() == digest.upper()]
    if len(matches) != 1:
        raise ValueError('The requested valid signing identity is missing or ambiguous')
    digest, name = matches[0]
    if installer and sum(row_name == name for _, row_name in rows) != 1:
        raise ValueError('Installer certificate name is ambiguous; use an isolated signing keychain')
    prefixes = INSTALLER_IDENTITIES if installer else APP_IDENTITIES
    if not name.startswith(prefixes) or not name.endswith(f'({team_id})'):
        raise ValueError('The signing identity is not the required Mac App Store type and team')
    return digest, name


def resolve_identity(requested, team_id, *, installer=False):
    # -p codesigning would hide Installer identities. Apple documents using -v.
    listing = run(['/usr/bin/security', 'find-identity', '-v']).stdout.decode()
    return choose_identity(listing, requested, team_id, installer=installer)


def decode_profile(path):
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= 4 * 1024 * 1024:
        raise ValueError('A readable distribution provisioning profile is required')
    decoded = run(['/usr/bin/security', 'cms', '-D', '-i', str(path)]).stdout
    try:
        profile = plistlib.loads(decoded)
    except (ValueError, TypeError, plistlib.InvalidFileException) as error:
        raise ValueError('The provisioning profile could not be decoded') from error
    if not isinstance(profile, dict):
        raise ValueError('Unexpected provisioning profile format')
    return profile


def profile_claims(profile, bundle_id, team_id, *, require_beta=False, now=None):
    validate_store_identity(bundle_id, team_id)
    now = now or datetime.now(timezone.utc)
    expiration = profile.get('ExpirationDate')
    if not isinstance(expiration, datetime):
        raise ValueError('Provisioning profile has no expiration date')
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=timezone.utc)
    if expiration <= now:
        raise ValueError('Provisioning profile is expired')
    teams = profile.get('TeamIdentifier', [])
    if not isinstance(teams, list) or team_id not in teams:
        raise ValueError('Provisioning profile belongs to a different team')
    platforms = profile.get('Platform', [])
    if not isinstance(platforms, list) or not any(p in {'OSX', 'macOS'} for p in platforms if isinstance(p, str)):
        raise ValueError('Provisioning profile is not for macOS')
    if 'ProvisionedDevices' in profile or profile.get('ProvisionsAllDevices'):
        raise ValueError('A device-limited or Developer ID profile is not a Store distribution profile')
    entitlements = profile.get('Entitlements', {})
    if not isinstance(entitlements, dict) or any(entitlements.get(key) for key in
            ('get-task-allow', 'com.apple.security.get-task-allow')):
        raise ValueError('A development/debug provisioning profile cannot be distributed')
    app_id = entitlements.get('com.apple.application-identifier', '')
    prefixes = profile.get('ApplicationIdentifierPrefix', [])
    if (not isinstance(prefixes, list) or not all(isinstance(p, str) and p.isalnum() for p in prefixes)
            or not isinstance(app_id, str) or app_id not in [f'{prefix}.{bundle_id}' for prefix in prefixes]):
        raise ValueError('Provisioning profile does not authorize the exact application ID')
    if entitlements.get('com.apple.developer.team-identifier') != team_id:
        raise ValueError('Provisioning profile team entitlement does not match')
    certificates = profile.get('DeveloperCertificates', [])
    if not isinstance(certificates, list) or not certificates or any(not isinstance(c, bytes) for c in certificates):
        raise ValueError('Provisioning profile has no authorized signing certificates')
    claims = {'com.apple.application-identifier': app_id,
              'com.apple.developer.team-identifier': team_id}
    if entitlements.get('beta-reports-active') is True:
        claims['beta-reports-active'] = True
    elif require_beta:
        raise ValueError('TestFlight requires a profile with beta-reports-active')
    # Do not copy wildcard keychain groups, app groups, device permissions or
    # unrelated service capabilities from the profile into the application.
    return claims


def profile_authorizes_certificate(profile, sha1):
    if sha1.upper() not in {hashlib.sha1(cert).hexdigest().upper()
                           for cert in profile['DeveloperCertificates']}:
        raise ValueError('Provisioning profile does not authorize this signing certificate')


def base_entitlements(*, helper=False):
    name = 'entitlements-worker.plist' if helper else 'entitlements-main.plist'
    return plistlib.loads((ROOT / 'packaging' / name).read_bytes())


def signed_entitlements(path):
    return plistlib.loads(run(['/usr/bin/codesign', '-d', '--entitlements', '-', '--xml', str(path)]).stdout)


def signature_details(path):
    result = run(['/usr/bin/codesign', '-d', '--verbose=4', str(path)])
    text = (result.stdout + result.stderr).decode('utf-8', errors='replace')
    fields = {}
    for line in text.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            fields.setdefault(key, []).append(value)
    return fields


def verify_distribution_signature(app, team_id):
    if not TEAM_ID.fullmatch(team_id or ''):
        raise ValueError('An explicit Apple Team ID is required')
    requirement = f'anchor apple generic and certificate leaf[subject.OU] = "{team_id}"'
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', '-R', requirement, str(app)])
    for path in (app, app / 'Contents/MacOS/WhaleReadWorker',
                 app / 'Contents/Frameworks/WhaleReadPurchase.dylib'):
        details = signature_details(path)
        authority = details.get('Authority', [''])[0]
        if details.get('TeamIdentifier') != [team_id] or not authority.startswith(APP_IDENTITIES):
            raise ValueError('Application, helper and purchase library require the same Store distribution team')


def leaf_certificate(path, output_prefix):
    run(['/usr/bin/codesign', '-d', '--extract-certificates', str(output_prefix), str(path)])
    return Path(str(output_prefix) + '0').read_bytes()
