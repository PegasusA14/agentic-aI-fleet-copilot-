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


def test_proposal_headline_is_exact():
    assert (
        texts.proposal_headline("open a remediation ticket", "host-a")
        == "Proposed: open a remediation ticket for host-a. "
        "It waits for your approval; nothing has changed yet."
    )


def test_action_texts_and_labels():
    assert texts.ACTION_NOT_ELIGIBLE == (
        "That action is not supported by the current evidence, so no proposal was made."
    )
    assert texts.ACTION_INSUFFICIENT_EVIDENCE == (
        "There is not enough recent evidence to propose that action, so no proposal was made."
    )
    assert texts.ACTION_LABELS == {
        "remediation_ticket": "open a remediation ticket",
        "replacement_flag": "flag the device for replacement",
        "upgrade_order": "order a hardware upgrade",
        "employee_notification": "notify the assigned employee",
    }


def test_unsupported_action_names_what_is_supported():
    text = texts.UNSUPPORTED_ACTION
    for phrase in ("one device at a time", "ticket", "replacement", "upgrade", "notification"):
        assert phrase in text
    assert "review screen" in text and "isn't available yet" not in text
