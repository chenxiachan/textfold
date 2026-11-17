"""Sentence boundaries used by both the reader's verbatim Brief level and the model's evidence indices."""
import unittest

from textfold.tokens import plain, split_sentences, tokenize_text


class Sentences(unittest.TestCase):
    def check_sentences(self, text, expected):
        toks = tokenize_text(text)
        ranges = split_sentences(toks)
        self.assertEqual([plain(toks[a:b], {}) for a, b in ranges], expected)
        # Every original token stays in exactly one sentence, in order.
        self.assertEqual([i for a, b in ranges for i in range(a, b)], list(range(len(toks))))
        self.assertTrue(all(a < b for a, b in ranges))

    def test_chinese_without_spaces(self):
        self.check_sentences("这是背景。实验显示吸附量下降。这个结果支持结论。",
                             ["这是背景。", "实验显示吸附量下降。", "这个结果支持结论。"])

    def test_fullwidth_questions_and_exclamations(self):
        self.check_sentences("结果可靠吗？可靠！继续验证", ["结果可靠吗？", "可靠！", "继续验证"])

    def test_ascii_stops_in_chinese(self):
        self.check_sentences("结果可靠吗?可靠!下一步继续验证.结果成立.",
                             ["结果可靠吗?", "可靠!", "下一步继续验证.", "结果成立."])

    def test_mixed_languages(self):
        self.check_sentences("结果成立。Next we test it.实验仍在继续。",
                             ["结果成立。", "Next we test it.", "实验仍在继续。"])

    def test_closing_quotes_and_brackets(self):
        self.check_sentences('他说：“结果可靠吗？”研究者回答：「可靠！」（附注：“已经验证。”）随后公布数据。',
                             ['他说：“结果可靠吗？”', '研究者回答：「可靠！」', '（附注：“已经验证。”）', '随后公布数据。'])
        self.check_sentences('他说："可靠!"随后公布数据。', ['他说："可靠!"', '随后公布数据。'])

    def test_repeated_stops_stay_with_their_sentence(self):
        self.check_sentences("真的吗？！当然！！随后验证。", ["真的吗？！", "当然！！", "随后验证。"])
        self.check_sentences("Really?! Yes!", ["Really?!", "Yes!"])

    def test_spaces_and_final_closers(self):
        self.check_sentences("结果成立。  下一步验证。", ["结果成立。", "下一步验证。"])
        self.check_sentences("他说：“结果成立。”", ["他说：“结果成立。”"])
        self.check_sentences("", [])

    def test_commas_semicolons_and_decimals_do_not_end_sentences(self):
        self.check_sentences("浓度为3.14%，温度为25度；条件相同：继续验证。结果成立。",
                             ["浓度为3.14%，温度为25度；条件相同：继续验证。", "结果成立。"])

    def test_english_abbreviations_initials_and_decimals(self):
        self.check_sentences("Dr. Smith et al. measured 3.14 units (see Fig. 2). Next we test it.",
                             ["Dr. Smith et al. measured 3.14 units (see Fig. 2).", "Next we test it."])
        self.check_sentences("A. Smith agrees. Next we test it.", ["A. Smith agrees.", "Next we test it."])
        self.check_sentences('He said, "It holds." Next we test it.',
                             ['He said, "It holds."', 'Next we test it.'])

    def test_a_sentence_opens_on_a_capital_of_any_alphabet(self):
        self.check_sentences("Он ушёл домой. Потом он вернулся.", ["Он ушёл домой.", "Потом он вернулся."])
        self.check_sentences("Він пішов. Потім повернувся.", ["Він пішов.", "Потім повернувся."])
        self.check_sentences("Es war spät. Über Nacht schneite es.", ["Es war spät.", "Über Nacht schneite es."])
        self.check_sentences("C'est fini. État des lieux.", ["C'est fini.", "État des lieux."])
        self.check_sentences("To jest dom. Żaden kot.", ["To jest dom.", "Żaden kot."])
        self.check_sentences("C'est fini. « Pourquoi ? » demanda-t-elle.", ["C'est fini.", "« Pourquoi ? » demanda-t-elle."])

    def test_an_initial_of_any_alphabet_ends_no_sentence(self):
        self.check_sentences("Писал А. Блок. Потом ушёл.", ["Писал А. Блок.", "Потом ушёл."])
        self.check_sentences("Laut Ö. Yılmaz gilt es. Dann nicht.", ["Laut Ö. Yılmaz gilt es.", "Dann nicht."])

    def test_chinese_with_english_abbreviations(self):
        self.check_sentences("参见Fig. 2，数值为3.14。结果成立。", ["参见Fig. 2，数值为3.14。", "结果成立。"])


if __name__ == "__main__":
    unittest.main()
