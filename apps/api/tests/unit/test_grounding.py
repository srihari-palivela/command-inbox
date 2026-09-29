from command_inbox.knowledge.grounding import unsupported_sentences
from command_inbox.knowledge.retrieve import or_query

SOURCE = "A copy of an account statement is emailed to the registered email address within one working day."


def test_supported_and_courtesy_sentences_pass_unsupported_claims_are_flagged():
    body = (
        "Dear Ravi,\n\nThank you for writing in.\n\n"
        "Your statement copy will be emailed to your registered email address within one working day [1].\n\n"
        "We will also waive your annual card fee and credit 500 to your account.\n\nWarm regards,"
    )
    assert unsupported_sentences(body, [SOURCE]) == [
        "We will also waive your annual card fee and credit 500 to your account."
    ]


def test_full_text_query_is_an_or_of_safe_terms():
    q = or_query("Please STOP cheque 004512!! it's urgent & (really) urgent: <script>")
    assert q == "please | stop | cheque | 004512 | urgent | really | script"
    assert all(t.isalnum() for t in q.split(" | "))
