"""The latest daily digest, named by /api/status and served by /api/file — and nothing beside it."""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from eki import digest, server


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read().decode()


def file_url(web, path):
    return web + "/api/file?path=" + urllib.parse.quote(str(path))


def test_no_digest_yet_is_none(web):
    assert json.loads(get(web + "/api/status")[1])["digest"] is None


def test_status_names_the_latest_page_and_it_is_served(web):
    (digest.folder() / "2026-09-24.md").write_text("# older\n")
    (digest.folder() / "2026-09-25.md").write_text("# eki, 2026-09-25\n\n## Landed and live\n")
    st = json.loads(get(web + "/api/status")[1])
    assert st["digest"] == str(digest.folder() / "2026-09-25.md")
    code, body = get(file_url(web, st["digest"]))
    assert code == 200 and "Landed and live" in body


def test_nothing_outside_the_digests_folder_is_served(web, home):
    folder = digest.folder()
    (folder / "2026-09-25.md").write_text("# page\n")
    (home / "self" / "notes.md").write_text("not a digest")
    (folder / "sub").mkdir()
    (folder / "sub" / "x.md").write_text("nested")
    (folder / "page.txt").write_text("not markdown")
    for path in (home / "self" / "notes.md", f"{folder}/../notes.md", f"{folder}/../../providers.json",
                 folder / "sub" / "x.md", folder / "page.txt", folder):
        with pytest.raises(urllib.error.HTTPError):
            get(file_url(web, path))
