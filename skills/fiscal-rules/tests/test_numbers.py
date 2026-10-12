import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from fiscal_check import to_num, run


class FiscalNumbers(unittest.TestCase):
    def test_malformed_cell_is_reported_to_user(self):
        report = run('금액 (단위: 원)\n\n| 사업 | 2025년 예산 |\n|---|---|\n| 표본 | 1,2 |\n')
        self.assertTrue(any(f.rule == '수치 표기 오류' for f in report.fails))

    def test_invalid_numeric_text_is_not_silently_repaired(self):
        for value in ['1,2', '10%20', '1원2', '1,,200', '1,200,', '1.2.3']:
            with self.subTest(value=value):
                self.assertIsNone(to_num(value))

    def test_valid_financial_notation(self):
        for text, value in [('1,200',1200), ('△1,200',-1200), ('(1,200)',-1200),
                            ('+12.5%',12.5), ('−2.5%',-2.5), ('１，２００원',1200), ('0',0)]:
            with self.subTest(text=text):
                self.assertEqual(to_num(text), value)
