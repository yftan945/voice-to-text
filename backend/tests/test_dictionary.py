from app import dictionary as d
from app.pipeline.postprocess import build_processor, to_traditional


def test_parse_terms_rules_and_comments():
    content = """
# 註解
Kubernetes
人工智慧 = 人工只能, 人工智能
OpenAI ＝ open AI，Open A I、偶喷AI

"""
    dic = d.parse(content)
    assert dic.terms == ["Kubernetes", "人工智慧", "OpenAI"]
    assert ("人工只能", "人工智慧") in dic.rules
    assert ("人工智能", "人工智慧") in dic.rules
    assert ("open AI", "OpenAI") in dic.rules
    assert ("Open A I", "OpenAI") in dic.rules
    assert ("偶喷AI", "OpenAI") in dic.rules


def test_parse_ignores_empty_and_self_rules():
    dic = d.parse("= 錯\n正 = 正, , 誤\n")
    assert dic.terms == ["正"]
    assert dic.rules == [("誤", "正")]


def test_replace_longest_first_and_no_chain():
    dic = d.parse("台積電 = 台機電, 台機\n台機電路 = 台機電路板")
    # 「台機電路板」要整個換成「台機電路」，而不是先命中較短的「台機電」
    assert d.apply("台機電路板與台機電", dic) == "台機電路與台積電"


def test_replace_ascii_word_boundary_and_case():
    dic = d.parse("Docker = doker\nGPT = GBT")
    assert d.apply("我用 Doker 跑 gbt，category 不變", dic) == "我用 Docker 跑 GPT，category 不變"
    assert d.apply("dokers", dic) == "dokers"  # 不會換到單字的一部分
    assert d.apply("中文doker中文", dic) == "中文Docker中文"  # 中文旁邊照樣換


def test_merge_first_dictionary_wins():
    a = d.parse("甲 = 錯")
    b = d.parse("乙 = 錯\n丙")
    merged = d.merge_all([a, b])
    assert merged.terms == ["甲", "乙", "丙"]
    assert d.apply("錯", merged) == "甲"


def test_hint_terms_length_limit():
    dic = d.parse("\n".join(f"詞{i:03d}" for i in range(200)))
    hints = d.hint_terms(dic, max_chars=50)
    assert sum(len(h) + 1 for h in hints) <= 50
    assert hints[0] == "詞000"


def test_add_rule_appends_to_existing_line():
    content = "人工智慧 = 人工只能\nKubernetes\n"
    out = d.add_rule(content, "人工智能", "人工智慧")
    assert out == "人工智慧 = 人工只能, 人工智能\nKubernetes\n"
    out = d.add_rule(out, "K8S", "Kubernetes")
    assert "Kubernetes = K8S" in out
    # 重複加入不變
    assert d.add_rule(out, "K8S", "Kubernetes") == out
    out = d.add_rule("", "錯", "正")
    assert out == "正 = 錯\n"


def test_add_rule_rejects_same_or_empty():
    import pytest

    with pytest.raises(ValueError):
        d.add_rule("", "同", "同")
    with pytest.raises(ValueError):
        d.add_rule("", "", "正")


def test_processor_opencc_then_dictionary():
    # OpenCC s2twp：簡體轉台灣用語；字典的正字原樣保留
    assert to_traditional("软件和网络") == "軟體和網路"
    dic = d.parse("台灣大哥大 = 台湾大哥大\n人工智慧 = 人工只能")
    process = build_processor(dic)
    # 誤聽寫簡體也能命中（會先轉成繁體再比對）
    assert process("台湾大哥大的人工只能软件") == "台灣大哥大的人工智慧軟體"
    # 英文不受影響
    assert process("Hello World") == "Hello World"


def test_fullwidth_punct_only_next_to_chinese():
    from app.pipeline.postprocess import fullwidth_punct

    assert fullwidth_punct("你好,我是Alex. 對嗎?") == "你好，我是Alex. 對嗎？"
    assert fullwidth_punct("Hello, world. Pi is 3.14, at 10:30") == "Hello, world. Pi is 3.14, at 10:30"
    assert fullwidth_punct("掃描QR Code的朋友們, 也歡迎") == "掃描QR Code的朋友們，也歡迎"


def test_prompt_leak_filter():
    from app.pipeline.asr import is_prompt_leak

    prompt = "以下是國語與英文夾雜的對話，請使用繁體中文。"
    assert is_prompt_leak("請使用繁體中文，請使用繁體中文。", prompt, [])
    assert is_prompt_leak("Atlassian、Jira", "", ["Atlassian", "Jira", "Confluence"])
    assert not is_prompt_leak("Atlassian", "", ["Atlassian", "Jira"])  # 只講一個詞不算
    assert not is_prompt_leak("我們用 Jira 管理工單", "", ["Atlassian", "Jira"])
    assert not is_prompt_leak("中文", prompt, [])  # 太短不判斷
    assert not is_prompt_leak("好", "", [])
