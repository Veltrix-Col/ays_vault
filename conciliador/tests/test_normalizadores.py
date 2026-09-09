from __future__ import annotations

import unittest

from conciliador.parsing.normalizadores import parse_cop_es


class ParseCopEsTests(unittest.TestCase):
    def test_sin_decimales(self):
        self.assertEqual(parse_cop_es("$529.579"), 529579.0)

    def test_con_decimales(self):
        self.assertEqual(parse_cop_es("$26.478,95"), 26478.95)

    def test_numero_ya_parseado(self):
        self.assertEqual(parse_cop_es(11400), 11400.0)
        self.assertEqual(parse_cop_es(11400.5), 11400.5)

    def test_valor_nulo(self):
        self.assertEqual(parse_cop_es(None), 0.0)

    def test_cadena_vacia(self):
        self.assertEqual(parse_cop_es(""), 0.0)

    def test_cadena_no_numerica(self):
        self.assertEqual(parse_cop_es("N/A"), 0.0)


if __name__ == "__main__":
    unittest.main()
