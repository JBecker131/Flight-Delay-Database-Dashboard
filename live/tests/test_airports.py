from live.airports import by_code, load


def test_airports_json_covers_bts_airports():
    assert len(load()["airports"]) == 362
    assert by_code()["CLT"]["tz"] == "America/New_York"
    assert by_code()["CLT"]["board"] == "CHARLOTTE"
    assert "AA" in load()["airlines"]
