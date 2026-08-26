#!/usr/bin/env python3
"""Efeito tem que ser AUDIVEL sob a voz. Trava nascida de defeito real (20/08/2026).

O whoosh foi gerado com volume=-14dB e ficou com RMS -40,5 dBFS, 26 dB abaixo da voz do
Thales: a mixagem rodava, o log dizia "6 efeitos", e nao se ouvia NADA. Medido, o delta
de energia na janela do efeito era 0,0 dB. "Existia" so no arquivo.

Faixa: -30 a -20 dBFS. Abaixo de -30 some sob a fala (-14 a -22 dBFS nas janelas
faladas); acima de -20 compete com ela.
"""
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import som_cortes as S

MIN_DB, MAX_DB = -30.0, -20.0


def rms_db(p):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-map", "0:a",
                        "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
                       capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float64) / 32768.0
    if a.size == 0:
        return None
    return 20 * np.log10(max(float(np.sqrt((a ** 2).mean())), 1e-9))


class TesteNivelDeSom(unittest.TestCase):

    def test_todo_efeito_e_audivel_e_nao_compete(self):
        for nome in S.EFEITOS:
            with self.subTest(efeito=nome):
                db = rms_db(S.SOM / nome)
                self.assertIsNotNone(db, f"{nome} sem audio")
                self.assertGreaterEqual(db, MIN_DB,
                    f"{nome} em {db:.1f} dBFS: some sob a voz (foi o defeito de 20/08)")
                self.assertLessEqual(db, MAX_DB,
                    f"{nome} em {db:.1f} dBFS: compete com a voz do Thales")

    def test_nao_estoura(self):
        for nome in S.EFEITOS:
            with self.subTest(efeito=nome):
                r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(S.SOM / nome),
                                    "-map", "0:a", "-f", "s16le", "-ac", "1",
                                    "-ar", "48000", "-"], capture_output=True)
                a = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float64) / 32768.0
                self.assertLess(float(np.abs(a).max()), 0.99, f"{nome} clipa")


if __name__ == "__main__":
    unittest.main(verbosity=2)
