def test_import():
    import verichart  # noqa: F401


def test_veritract_dependency_available():
    import veritract

    # verichart needs the pipeline-manifest API added in veritract 0.2.
    assert hasattr(veritract, "build_manifest")
    assert hasattr(veritract, "ExtractionResult")
