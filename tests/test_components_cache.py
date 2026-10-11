"""Cached card loading: own() reloads only cards whose files changed; git asked once per folder."""
import json
import os
import subprocess

import pytest

from eki import components, workflow


def card(root, name, about='does a thing'):
    d = root / 'components' / name
    d.mkdir(parents=True, exist_ok=True)
    (d / 'component.json').write_text(json.dumps({'kind': 'service', 'about': about, 'command': ['true']}))
    return d


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'proj'
    for i in range(16):
        card(root, f'c{i:02d}')
    return root


@pytest.fixture
def loads(monkeypatch):
    seen = []
    real = components.load

    def counting(folder, scope):
        seen.append(folder.name)
        return real(folder, scope)
    monkeypatch.setattr(components, 'load', counting)
    return seen


def test_second_own_loads_nothing(project, loads):
    found, errors = components.own(project)
    assert len(found) == 16 and not errors, errors
    assert sorted(loads) == sorted(found)          # each card loaded once in the call
    loads.clear()
    again, _ = components.own(project)
    assert list(again) == list(found) and loads == []


def test_rewritten_card_reloads_alone(project, loads):
    components.own(project)
    loads.clear()
    f = project / 'components' / 'c03' / 'component.json'
    card(project, 'c03', about='a new line about it')
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    found, _ = components.own(project)
    assert loads == ['c03']
    assert found['c03'].spec['about'] == 'a new line about it'


def test_added_and_removed_folders_show(project):
    components.own(project)
    card(project, 'zz-new')
    found, _ = components.own(project)
    assert 'zz-new' in found and len(found) == 17
    for f in (project / 'components' / 'c00').iterdir():
        f.unlink()
    (project / 'components' / 'c00').rmdir()
    found, _ = components.own(project)
    assert 'c00' not in found and len(found) == 16


def test_broken_card_reported_each_call(project):
    (project / 'components' / 'bad').mkdir()
    for _ in range(2):
        found, errors = components.own(project)
        assert 'bad' not in found and [e for e in errors if e[0].endswith('/bad')]


def test_returned_spec_is_a_copy(project):
    found, _ = components.own(project)
    found['c01'].spec['about'] = 'changed by a caller'
    found['c01'].spec['command'].append('x')
    again, _ = components.own(project)
    assert again['c01'].spec['about'] == 'does a thing' and again['c01'].spec['command'] == ['true']


def test_pool_card_follows_its_pool_component(tmp_path, loads):
    pool = components.pool_dir() / 'shared'
    pool.mkdir(parents=True)
    (pool / 'component.json').write_text(json.dumps({'kind': 'service', 'about': 'old', 'command': ['true']}))
    root = tmp_path / 'p2'
    d = root / 'components' / 'shared'
    d.mkdir(parents=True)
    (d / 'component.json').write_text(json.dumps({'pool': 'shared'}))
    found, _ = components.own(root)
    assert found['shared'].card == d and found['shared'].spec['about'] == 'old'
    loads.clear()
    components.own(root)
    assert loads == []
    (pool / 'component.json').write_text(json.dumps({'kind': 'service', 'about': 'newer one', 'command': ['true']}))
    found, _ = components.own(root)
    assert loads == ['shared'] and found['shared'].spec['about'] == 'newer one'


def test_find_uses_the_cache(project, loads):
    components.find(project)
    loads.clear()
    found, _ = components.find(project)
    assert len(found) == 16 and loads == []


def test_git_asked_once_per_folder(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    first = components.project_root(repo)
    eki_first = workflow.is_eki(repo)

    def no(*a, **k):
        raise AssertionError('no subprocess after the first call')
    monkeypatch.setattr(subprocess, 'run', no)
    assert components.project_root(repo) == first == repo.resolve()
    assert workflow.is_eki(repo) is eki_first is False


def test_is_eki_fresh_when_source_moves(tmp_path, monkeypatch):
    repo = tmp_path / 'src'
    repo.mkdir()
    assert workflow.is_eki(repo) is False
    monkeypatch.setenv('EKI_SOURCE', str(repo))
    assert workflow.is_eki(repo) is True
