from notify import Pushover, Caller


def test_pushover_disabled_without_keys():
    assert Pushover(None, None).enabled is False
    assert Pushover("u", None).enabled is False
    assert Pushover("u", "t").enabled is True

def test_pushover_noops_when_disabled():
    # returns None, no network call, no exception
    assert Pushover(None, None).emergency("t", "m") is None
    Pushover(None, None).cancel("receipt123")  # must not raise

def test_caller_disabled_without_full_creds():
    assert Caller(None, None, None, None).enabled is False
    assert Caller("AC", "tok", "+1", None).enabled is False
    assert Caller("AC", "tok", "+1", "+82").enabled is True

def test_caller_noops_when_disabled():
    Caller(None, None, None, None).call("hi")  # must not raise
