from lumi_collector.power import hold_percent, percent_from_ina219_word


def test_ina219_word_from_the_ups_is_a_nearly_full_cell():
    # Bus register as SMBus returned it: 4.152 V → about 96%.
    pct = percent_from_ina219_word(0x7220)
    assert pct is not None
    assert 95 < pct < 97


def test_ina219_zero_word_is_not_an_empty_cell():
    assert percent_from_ina219_word(0) is None


def test_displayed_percent_does_not_flicker_around_a_boundary():
    shown = hold_percent(None, 85.4)
    assert shown == 85
    assert hold_percent(shown, 85.6) == 85
    assert hold_percent(shown, 86.2) == 86
    assert hold_percent(86, 85.2) == 86
