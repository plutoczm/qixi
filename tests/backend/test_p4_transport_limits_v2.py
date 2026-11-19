"""Latest user-approved P4 transport thresholds (2026-10-31)."""

WALK_LIMIT_METERS = 1500
DRIVE_LIMIT_SECONDS = 900


def transport_pass(walk_meters, drive_seconds):
    walk_ok = type(walk_meters) is int and walk_meters <= WALK_LIMIT_METERS
    drive_ok = type(drive_seconds) is int and drive_seconds <= DRIVE_LIMIT_SECONDS
    return walk_ok, drive_ok, walk_ok or drive_ok


def test_walk_distance_boundary_and_drive_time_boundary():
    assert transport_pass(1500, 901) == (True, False, True)
    assert transport_pass(1501, 900) == (False, True, True)
    assert transport_pass(1501, 901) == (False, False, False)
    assert transport_pass(1499, 899) == (True, True, True)


def test_unknown_and_wrong_type_never_pass():
    assert transport_pass(None, 900) == (False, True, True)
    assert transport_pass(1500, None) == (True, False, True)
    assert transport_pass(True, 900) == (False, True, True)
    assert transport_pass(1500, True) == (True, False, True)
    assert transport_pass(None, None) == (False, False, False)
