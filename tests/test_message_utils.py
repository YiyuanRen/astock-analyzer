from app.bot.message_utils import Mention, extract_command_text, is_bot_mentioned, strip_mentions

BOT = "ou_bot"


def test_p2p_passthrough():
    assert extract_command_text("p2p", " Q 000001 ", [], BOT) == "Q 000001"


def test_group_with_bot_mention_strips_placeholder():
    ms = [Mention("@_user_1", BOT, "机器人")]
    assert extract_command_text("group", "@_user_1 Q 000001", ms, BOT) == "Q 000001"
    assert extract_command_text("group", "Q 000001 @_user_1", ms, BOT) == "Q 000001"


def test_group_without_mention_ignored():
    assert extract_command_text("group", "Q 000001", [], BOT) is None


def test_group_mentions_other_person_only_ignored():
    ms = [Mention("@_user_1", "ou_other", "张三")]
    assert extract_command_text("group", "@_user_1 Q 000001", ms, BOT) is None


def test_group_multiple_mentions_all_stripped():
    ms = [Mention("@_user_1", "ou_other"), Mention("@_user_2", BOT)]
    assert extract_command_text("group", "@_user_1 @_user_2 L", ms, BOT) == "L"


def test_at_all_does_not_count_as_bot_mention():
    ms = [Mention("@_all")]
    assert not is_bot_mentioned(ms, BOT)
    assert extract_command_text("group", "@_all 开会", ms, BOT) is None


def test_unknown_bot_id_falls_back_to_any_mention():
    ms = [Mention("@_user_1", "ou_x")]
    assert extract_command_text("group", "@_user_1 L", ms, None) == "L"
    assert extract_command_text("group", "L", [], None) is None


def test_strip_leftover_placeholders_without_mentions_list():
    assert strip_mentions("@_user_3 Q  000001", []) == "Q 000001"
