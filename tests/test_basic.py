from ovos_stt_apple import normalize_lang


def test_normalize():
    assert normalize_lang("nl") == "nl-NL"
    assert normalize_lang("nl_nl") == "nl-NL"
    assert normalize_lang("en-us") == "en-US"
    assert normalize_lang(None) == "en-US"
