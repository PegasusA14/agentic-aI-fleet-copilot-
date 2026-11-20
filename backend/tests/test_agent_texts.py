from fleet_copilot.agent import texts


def test_entry_out_of_range_agrees_in_number():
    assert texts.entry_out_of_range(1) == "That list has 1 entry. Which one do you mean?"
    assert texts.entry_out_of_range(3) == "That list has 3 entries. Which one do you mean?"


def test_fixed_texts_exist_and_are_plain():
    for name in (
        "ROUND_LIMIT_NOTE",
        "NEED_REFERENCE",
        "WHICH_LIST",
        "DEFAULT_CLARIFICATION",
        "CLARIFICATION_HEADLINE",
    ):
        value = getattr(texts, name)
        assert value.strip() and "<" not in value and ">" not in value
