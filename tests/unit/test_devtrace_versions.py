import pytest

from app.devtrace.versions import VersionError, in_range, parse_version, version_key


def test_numeric_not_lexicographic_ordering():
    assert version_key("0.99.1") < version_key("0.100.0") < version_key("0.110.0") < version_key("0.162.0")
    assert version_key("0.9.0") < version_key("0.10.0")
    assert version_key("2.9.0") < version_key("2.14.1")
    assert version_key("1.9999.9999") < version_key("2.0.0")


def test_accepts_v_prefix_and_ignores_prerelease_tag():
    assert parse_version("v2.14.1") == (2, 14, 1)
    assert version_key("2.14.0b1") == version_key("2.14.0") and version_key("1.0.0rc2") == version_key("1.0.0")


@pytest.mark.parametrize("bad", ["", "1.2", "1.2.3.4", "abc", "1.x.3", "99999.0.0", "-1.0.0", " ", "1.2.3; DROP", "0.1\n.0", "0.1.0\n0.2.0", None])
def test_invalid_versions_rejected(bad):
    with pytest.raises(VersionError):
        parse_version(bad)


def test_range_is_exclusive_lower_inclusive_upper():
    assert not in_range("0.100.0", gt="0.100.0", lte="0.110.0")
    assert in_range("0.101.0", gt="0.100.0", lte="0.110.0")
    assert in_range("0.110.0", gt="0.100.0", lte="0.110.0")
    assert not in_range("0.110.1", gt="0.100.0", lte="0.110.0")
    assert in_range("5.0.0") and in_range("0.1.0", lte="0.2.0") and in_range("0.3.0", gt="0.2.0")


def test_surrounding_whitespace_is_stripped_but_embedded_is_not():
    assert parse_version("  0.1.0\n") == (0, 1, 0)
