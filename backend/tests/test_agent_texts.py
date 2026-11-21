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
        "NO_SUGGESTIONS",
        "ADVISE_NEEDS_DEVICES",
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


def test_unsupported_action_says_what_to_ask_instead_and_has_no_review_screen():
    assert texts.UNSUPPORTED_ACTION == (
        "I can't do that, but I can recommend actions for devices you're looking at "
        "(ask 'what should I do?'), or propose one action for one device."
    )
    assert "review screen" not in texts.UNSUPPORTED_ACTION.lower()


def test_the_no_qualifying_action_note_names_the_requested_action():
    assert texts.no_qualifying_note("remediation_ticket") == (
        "None of these devices qualifies for a remediation ticket; here is what the evidence supports."
    )
    assert "a hardware upgrade" in texts.no_qualifying_note("upgrade_order")


def test_advise_texts_are_exact():
    assert (
        texts.NO_SUGGESTIONS == "None of these devices supports one of the four actions right now."
    )
    assert texts.ADVISE_NEEDS_DEVICES == "Which devices do you want recommendations for?"
