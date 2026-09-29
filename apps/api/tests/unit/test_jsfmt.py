from command_inbox.modules.insights.jsfmt import grouped


def test_grouping_follows_the_tenant_locale():
    assert grouped(1234567, "en-IN") == "12,34,567"
    assert grouped(1234567, "en-GB") == "1,234,567"
    assert grouped(-950, "en-US") == "-950"
