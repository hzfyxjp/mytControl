from app.script_timing import ScriptTimings


def test_script_timings_defaults():
    t = ScriptTimings()
    assert t.start_delay == 0.1
    assert t.sleep_long == 2.0
    assert t.sleep_mid == 1.0
    assert t.sleep_short == 0.6
    assert t.find_timeout == 20.0
    assert t.page_timeout == 25.0
    assert t.long_timeout == 30.0
    assert t.poll_interval == 0.2
    assert t.click_timeout == 15.0
    assert t.short_timeout == 10.0


def test_script_timings_clamp_and_roundtrip():
    t = ScriptTimings.from_dict(
        {
            "start_delay": -1,
            "sleep_long": 999,
            "find_timeout": 8,
            "poll_interval": 0.01,
        }
    )
    assert t.start_delay == 0.0
    assert t.sleep_long == 60.0
    assert t.find_timeout == 8.0
    assert t.click_timeout == 8.0
    assert t.poll_interval == 0.05
    again = ScriptTimings.from_dict(t.to_dict())
    assert again.to_dict() == t.to_dict()
