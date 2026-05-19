import urst


def test_urst_is_importable() -> None:
    import urst  # pyright: ignore[reportUnusedImport] # noqa: F401

    assert isinstance(urst, object)


def test_that_we_have_an_instance_of_urst() -> None:
    assert isinstance(urst, object)


def test_urst_version() -> None:
    assert isinstance(urst.__version__, str)
    assert urst.__version__ != ""
