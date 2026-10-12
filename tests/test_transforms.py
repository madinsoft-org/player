from gds_migrator.geometry.transforms import Point, Transform


def test_identity_transform_is_noop() -> None:
    transform = Transform()
    point = Point(3.0, -4.0)
    result = transform.apply(point)
    assert (result.x, result.y) == (3.0, -4.0)


def test_translation() -> None:
    transform = Transform(origin=Point(10.0, 20.0))
    result = transform.apply(Point(1.0, 2.0))
    assert (result.x, result.y) == (11.0, 22.0)


def test_rotation_90_degrees() -> None:
    transform = Transform(rotation=90.0)
    result = transform.apply(Point(1.0, 0.0))
    assert round(result.x, 9) == 0.0
    assert round(result.y, 9) == 1.0


def test_mirror_x_flips_y() -> None:
    transform = Transform(mirror_x=True)
    result = transform.apply(Point(1.0, 2.0))
    assert (result.x, result.y) == (1.0, -2.0)


def test_magnification_scales_point() -> None:
    transform = Transform(magnification=2.0)
    result = transform.apply(Point(3.0, 4.0))
    assert (result.x, result.y) == (6.0, 8.0)


def test_compose_matches_sequential_apply() -> None:
    outer = Transform(origin=Point(5.0, -5.0), rotation=90.0)
    inner = Transform(origin=Point(2.0, 3.0), mirror_x=True)
    composed = outer.compose(inner)

    probe = Point(1.0, 1.0)
    expected = outer.apply(inner.apply(probe))
    actual = composed.apply(probe)

    assert round(actual.x, 9) == round(expected.x, 9)
    assert round(actual.y, 9) == round(expected.y, 9)
