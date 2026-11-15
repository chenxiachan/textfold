"""Greek text: its own alphabet, and a question that ends on ; (or its own sign, U+037E), not on ?.

A Greek text is read as Greek, not as English; its sentences open on a Greek capital, and a ; after a Greek word ends
one, while a ; in an English sentence stays a semicolon.

Run from the repo root:
    python3 -m unittest discover tests
"""
import unittest

from textfold import langs
from textfold.tokens import plain, split_sentences, tokenize_text

EL = """Οι αναγνώστες συχνά διαβάζουν βιαστικά τα μεγάλα κείμενα και χάνουν το επιχείρημα. Μια σκάλα από επίπεδα τους
βοηθά να δουν το σύνολο. Πώς όμως κρατάμε τα λόγια του συγγραφέα; Κάθε επίπεδο κρατά το από κάτω του."""


def sentences(text):
    toks = tokenize_text(text)
    return [plain(toks[a:b], {}) for a, b in split_sentences(toks)]


class GreekTest(unittest.TestCase):
    def test_a_greek_text_is_read_as_greek(self):
        self.assertEqual(langs.detect(EL), "el")
        self.assertEqual(langs.detect("Ἐν ἀρχῇ ἦν ὁ λόγος, καὶ ὁ λόγος ἦν πρὸς τὸν θεόν, καὶ θεὸς ἦν ὁ λόγος."), "el")

    def test_other_texts_are_unchanged(self):
        self.assertEqual(langs.detect("The readers skim long papers, and they miss the argument of the paper that is in it."), "en")
        self.assertEqual(langs.detect("Читатели пробегают длинные статьи и упускают из виду главный аргумент этой работы."), "ru")

    def test_sentences_open_on_a_greek_capital(self):
        self.assertEqual(sentences("Αυτή είναι η πρώτη. Ήρθε η δεύτερη. Όλα καλά."),
                         ["Αυτή είναι η πρώτη.", "Ήρθε η δεύτερη.", "Όλα καλά."])

    def test_a_greek_question_ends_on_a_semicolon(self):
        self.assertEqual(sentences("Τι κάνεις; Είμαι καλά."), ["Τι κάνεις;", "Είμαι καλά."])
        self.assertEqual(sentences("Τι κάνεις; Είμαι καλά."), ["Τι κάνεις;", "Είμαι καλά."])
        self.assertEqual(len(sentences(EL)), 4)

    def test_an_english_semicolon_ends_no_sentence(self):
        self.assertEqual(sentences("It works; It is cheap."), ["It works; It is cheap."])

    def test_a_greek_initial_ends_no_sentence(self):
        self.assertEqual(sentences("Το έγραψε ο Γ. Σεφέρης. Ήταν ποιητής."), ["Το έγραψε ο Γ. Σεφέρης.", "Ήταν ποιητής."])

    def test_greek_quotes_stay_with_their_sentence(self):
        self.assertEqual(sentences("Είπε «ναι.» Μετά έφυγε."), ["Είπε «ναι.»", "Μετά έφυγε."])

    def test_a_reader_of_greek(self):
        self.assertEqual(langs.name("el"), "Greek")
        self.assertEqual(langs.natives()["el"], "Ελληνικά")
        self.assertFalse(langs.cjk("el"))
        self.assertEqual(langs.stop("el"), ".")
        self.assertEqual(langs.budget("el")["unit"], "words")
        self.assertIn(";", langs.punctuation_rule("el"))


if __name__ == "__main__":
    unittest.main()
