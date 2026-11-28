"""«Что нового»: запись для каждой выпускаемой версии."""
from bypasshub import __version__, github
from bypasshub.changelog import CHANGES, latest


def test_current_version_described():
    """Выпуская версию, добавьте её изменения в начало bypasshub/changelog.py."""
    assert latest().version == __version__, "нет записи «Что нового» для версии " + __version__
    assert all(rel.items and all(item.strip() for item in rel.items) for rel in CHANGES)


def test_versions_newest_first():
    versions = [github.parse_version(rel.version) for rel in CHANGES]
    assert versions == sorted(versions, reverse=True) and len(set(versions)) == len(versions)
