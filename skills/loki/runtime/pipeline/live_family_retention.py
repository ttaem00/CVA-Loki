"""Relocate one completed, live-linked producer family without running a job.

The live retention owner supplies the archive/phase and owns its location CAS.
This helper only copies frozen originals, transports settled MM receipts, and
publishes the existing producer index. Incomplete/edited families stay hot.
"""

from __future__ import annotations

from contextlib import closing

import hashlib

import json

import os

import re

import sqlite3

from . import vod_search_index as store

from .evidence_world.contracts import digest, native_path

SCHEMA = 'loom.live-family-retention.v1'

ROUTE_SCHEMA = 'loom.retained-report-route.v1'

MAX_FILES = 16_000

MAX_REPORTS = 32

MAX_RECEIPT_BYTES = 8_000_000

def _json(path, *, expected_sha256=None):
    path = store._plain(path)
    if not path.is_file() or path.stat().st_size > MAX_RECEIPT_BYTES:
        raise ValueError('family_receipt_invalid')
    with path.open('rb') as source:
        raw = source.read(MAX_RECEIPT_BYTES + 1)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError('family_receipt_invalid')
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('family_receipt_changed')
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('family_receipt_invalid')
    return value

def _check_plan(plan):
    if (plan.get('schema_version') != SCHEMA or digest({k:v for k,v in plan.items() if k != 'plan_sha256'}) != plan.get('plan_sha256')
            or not 0 < len(plan.get('members', [])) <= MAX_FILES
            or not 0 < len(plan.get('reports', [])) <= MAX_REPORTS):
        raise ValueError('family_plan_changed')
    if 'staged_plan_sha256' in plan:
        staged = plan['staged_plan_sha256']
        if (not isinstance(staged, str) or not re.fullmatch(r'[a-f0-9]{64}', staged)
                or staged == plan['plan_sha256'] or not isinstance(plan.get('registered_original'), dict)):
            raise ValueError('family_plan_changed')
    return store._roots(plan['hot_roots']), store._roots(plan['cold_roots'])

def _route_reports(plan):


    return {report['row']['base']: report for report in plan['reports']}.values()

def _trusted_path_texts(path):
    """Equivalent local-drive spellings of an already admitted path; no I/O."""
    text = str(path)
    values = {text}
    if os.name == 'nt':
        if re.match(r'^\\\\\?\\[A-Za-z]:\\', text):
            values.add(text[4:])
        elif re.match(r'^[A-Za-z]:\\', text):
            values.add('\\\\?\\'+text)
    return values

def _trusted_roots_match(value, roots):
    return (isinstance(value, dict) and set(value) == set(roots)
        and all(isinstance(value[key], str) and value[key] in _trusted_path_texts(path) for key,path in roots.items()))

def published_report_routes(*, work_dir, hot_roots, cold_roots, cold_database, known_reports=(), base=None, video_no=None):
    """Read existing producer status; a missing cold mount never revives hot copies.

    These are private catalog locators, not HTTP payloads. Only the normal
    configured cold roots are admitted. A held route still suppresses the old
    family, while unrelated and un-enrolled reports retain their normal path.
    """
    selected_base = base
    if selected_base is not None and (not isinstance(selected_base, str) or not selected_base
            or any(char in selected_base for char in '/\\\x00')):
        raise ValueError('retention_report_identity_invalid')
    if video_no is not None and (not isinstance(video_no, str) or not re.fullmatch(r'[0-9]+', video_no)):
        raise ValueError('retention_report_identity_invalid')
    routes = {}
    publications = {}
    cold_records = {}
    database = store.default_database_path(work_dir)
    known_identities = []



    for known in known_reports:
        if not isinstance(known, (tuple, list)) or len(known) != 2:
            continue
        base, video = known
        if selected_base is not None and base != selected_base:
            continue
        if video_no is not None and video != video_no:
            continue
        if (not isinstance(video, str) or not video.isdigit() or not isinstance(base, str)
                or base.split('_', 1)[0] != video or any(char in base for char in '/\\\x00')):
            continue
        known_identities.append((base, video))
    try:
        with closing(store._connect(database, readonly=True)) as db:
            store._check_schema(db, 'hot')
            query = "SELECT * FROM reports WHERE vector_mode='producer_memory' AND family_id<>''"
            parameters = ()
            if selected_base is not None:
                query += ' AND base=?'
                parameters = (selected_base,)
            if video_no is not None:
                query += ' AND video_no=?'
                parameters += (video_no,)
            rows = [dict(row) for row in db.execute(query + ' LIMIT 10000', parameters)]
    except (OSError, sqlite3.Error, ValueError):
        rows = []
    statuses = store.read_statuses(database, [base for base, _video in known_identities]
                                  + [row['base'] for row in rows])
    for base, video in known_identities:
        marker = statuses.get(base, {})
        if isinstance(marker, dict):
            marker = marker.get('retention')
            if isinstance(marker, dict) and marker.get('schema_version') == ROUTE_SCHEMA:
                routes[base] = dict(video_no=video, base=base, available=False)
    for row in rows:
        try:
            marker = statuses.get(row['base'], {}).get('retention')
            if not isinstance(marker, dict) or marker.get('schema_version') != ROUTE_SCHEMA:
                continue


            if (not isinstance(row['video_no'], str) or not row['video_no'].isdigit()
                    or not isinstance(row['base'], str) or row['base'].split('_', 1)[0] != row['video_no']
                    or any(char in row['base'] for char in '/\\\x00')):
                continue
            route = dict(video_no=row['video_no'], base=row['base'], available=False)
            routes.setdefault(row['base'], dict(route))
            snapshot = marker['snapshot']
            if (marker['state'] != 'active' or marker['video_no'] != row['video_no'] or marker['base'] != row['base']
                    or marker['report_key'] != row['report_key']
                    or snapshot['row_sha256'] != store._digest(row)
                    or snapshot['report_key'] != row['report_key']):
                continue
            hot, cold = store._roots(hot_roots), store._roots(cold_roots)
            if (set(hot) != {'work', 'output', 'site'} or 'work' not in cold or not cold_database
                    or hot['work'] != store._plain(work_dir)
                    or not _trusted_roots_match(marker['hot_roots'], hot) or not _trusted_roots_match(marker['cold_roots'], cold)
                    or marker['roots_sha256'] != digest(marker['cold_roots'])
                    or marker['hot_database'] not in _trusted_path_texts(database)
                    or marker['cold_database'] not in _trusted_path_texts(store._plain(cold_database))
                    or not store._plain(cold_database).is_relative_to(cold['work'])):
                continue
            publication = marker['publication_ref']
            archive = marker['archive']
            if (not isinstance(archive, str)
                    or not re.fullmatch(r'(?:[a-f0-9]{32}-[1-9]\d{0,15}|vod-'+row['video_no']+r'-[a-f0-9]{32})', archive)
                    or publication['ref'] != dict(root='work', relative='_vod-search/retention/'+archive+'.json')
                    or not isinstance(publication['sha256'], str)
                    or not re.fullmatch(r'[a-f0-9]{64}', publication['sha256'])):
                continue
            receipt_path = store._resolve(publication['ref'], cold)
            cache_key = (str(receipt_path), publication['sha256'])
            receipt = publications.get(cache_key)
            if receipt is None:
                with native_path(receipt_path).open('rb') as handle:
                    content = handle.read(MAX_RECEIPT_BYTES + 1)
                if len(content) > MAX_RECEIPT_BYTES or hashlib.sha256(content).hexdigest() != publication['sha256']:
                    continue
                receipt = json.loads(content.decode('utf-8-sig'))
                publications[cache_key] = receipt
            if receipt.get('schema_version') != SCHEMA or receipt.get('state') != 'published':
                continue
            plan = receipt['plan']


            if (plan['archive'] != archive or plan['video_no'] != row['video_no']
                    or not _trusted_roots_match(plan['hot_roots'], hot) or not _trusted_roots_match(plan['cold_roots'], cold)
                    or plan['hot_database'] not in _trusted_path_texts(database)
                    or plan['cold_database'] not in _trusted_path_texts(store._plain(cold_database))
                    or receipt['plan_sha256'] != plan['plan_sha256']
                    or not any(report['snapshot'] == snapshot for report in plan['reports'])):
                continue
            _check_plan(plan)
            if cache_key not in cold_records:
                route_keys = [report['row']['report_key'] for report in _route_reports(plan)]
                listing = store.list_registered_reports(database_path=cold_database, roots=cold,
                                                        report_keys=route_keys)
                if listing['tier'] != 'cold':
                    continue
                cold_records[cache_key] = {record['report_key']:record for record in listing['reports']}
            matched = cold_records[cache_key].get(row['report_key'])
            if not matched or matched['video_no'] != row['video_no'] or matched['base'] != row['base']:
                continue


            source_members = [dict(ref=dict(member['ref']), sha256=member['sha256']) for member in plan['members']]
            route.update(matched, available=True, source_work_root=cold['work'],
                source_roots=dict(cold), source_members=source_members)
            source = snapshot['files'].get('stt')
            if source is not None:
                route['source_srt_path'] = store._resolve(source['ref'], cold)
            chat_ref = dict(root='work', relative=row['video_no']+'/'+row['video_no']+'_chat.log.json')
            chat = next((member for member in plan['members'] if member['ref'] == chat_ref), None)
            if chat is not None:
                route['source_chat_sha256'] = chat['sha256']
            routes[row['base']] = route
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
            continue
    return routes
