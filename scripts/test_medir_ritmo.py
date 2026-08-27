#!/usr/bin/env python3
"""Teste do medidor de ritmo. ESCRITO ANTES do medidor, e tem que falhar antes de passar.

O contrato e simples e vem de medicao, nao de gosto:

  - as tres referencias do Vaibhav (o que o Julio mandou como "dinamico") pontuam
    entre 18 e 28 cortes/min. Se o medidor nao concordar com elas, o medidor esta errado.
  - a ref de HOOK do Pedro Sobral pontua BAIXO (2,1 cortes/min). Ela e a armadilha: e
    referencia da Jheni, mas de hook, nao de ritmo. Um medidor que a aprovasse como
    "dinamica" estaria medindo outra coisa.
  - os quatro ads de hoje REPROVAM. Se algum passar, o alvo esta frouxo demais pra
    mudar qualquer coisa.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import medir_ritmo as MR

from caminhos import DADOS as _D
REFS = _D / "refs"
from caminhos import OUTPUT as OUT

ADS_HOJE = [
    OUT / "jh13v2_espuma_roxa_v2composite_9x16.mp4",
    OUT / "jh14v2_oficial_13_v2composite_9x16.mp4",
    OUT / "jh15v2_neon_creme_v2composite_9x16.mp4",
    OUT / "jh16v2_espuma_roxa_v2composite_9x16.mp4",
]


class TesteMedidor(unittest.TestCase):

    def test_referencias_pontuam_na_faixa_dinamica(self):
        for i in (1, 2, 3):
            p = REFS / "vaibhav" / f"ref{i}.mp4"
            with self.subTest(ref=p.name):
                self.assertTrue(p.exists(), f"fixture ausente: {p}")
                m = MR.medir(p)
                self.assertGreaterEqual(m["cortes_min"], 18.0,
                                        f"{p.name} deu {m['cortes_min']:.1f}/min")
                self.assertLessEqual(m["cortes_min"], 28.0,
                                     f"{p.name} deu {m['cortes_min']:.1f}/min")

    def test_gate_APROVA_as_referencias(self):
        """O teste que faltava, e que custou uma rodada.

        Eu tinha conferido que as refs pontuam alto em cortes/min, mas nunca que o
        VEREDITO as aprova. Nao aprovava: o teto de "maior plano acima de 6s" reprovava
        a ref1, que segura um plano de 13,2s e mesmo assim faz 27,8 cortes/min. Um gate
        mais duro que a referencia reprovaria uma peca indistinguivel dela.

        Regra que fica: todo gate se valida contra a REFERENCIA que ele imita, nao so
        contra o defeito que ele caca.
        """
        for i in (1, 2, 3):
            p = REFS / "vaibhav" / f"ref{i}.mp4"
            with self.subTest(ref=p.name):
                m = MR.medir(p)
                ok, motivos = MR.aprova(m)
                self.assertTrue(ok, f"o gate REPROVOU a referencia {p.name}: {motivos}. "
                                    f"Criterio mais duro que a referencia esta errado.")

    def test_ref_de_hook_nao_e_ref_de_ritmo(self):
        p = REFS / "sobral" / "ref_hook.mp4"
        self.assertTrue(p.exists(), f"fixture ausente: {p}")
        m = MR.medir(p)
        self.assertLess(m["cortes_min"], 6.0,
                        "a ref de hook do Sobral e quase sem corte; se ela pontuar alto, "
                        "o medidor esta contando movimento e nao corte")

    def test_ads_de_hoje_reprovam(self):
        for p in ADS_HOJE:
            with self.subTest(ad=p.name):
                if not p.exists():
                    self.skipTest(f"ad ainda nao construido: {p.name}")
                m = MR.medir(p)
                self.assertFalse(MR.aprova(m)[0],
                                 f"{p.name} passou com {m['cortes_min']:.1f}/min e plano "
                                 f"medio {m['plano_medio']:.2f}s: alvo frouxo demais")

    def test_criterio_do_gate_tambem_reprova_ad_lento(self):
        """O teste acima mede pelo caminho CEGO, que o gate nao usa mais.

        Desde 27/08/2026 o gate cruza plano e imagem (`cortes_confirmados`), porque a
        deteccao pura errava nos dois sentidos: contou fundo desfocado piscando como 10
        cortes e depois deixou de ver `orig -> cheio` num anuncio escuro. Trocar o
        criterio do gate sem trocar o do teste deixaria a calibragem cobrindo um caminho
        morto: o teste passaria verde enquanto o gate real virava carimbo.

        jh14 e jh15 sao os ads que o Julio e a Jheni acharam lentos. Eles tem que
        reprovar pelo criterio NOVO tambem, senao o afrouxamento passou.
        """
        lentos = [("jh14v2_oficial_13", 16.0), ("jh15v2_neon_creme", 16.0)]
        checados = 0
        for nome, piso in lentos:
            v = OUT / f"{nome}_v2composite_9x16.mp4"
            j = OUT / f"{nome}_footage_1x_ritmo.json"
            if not (v.exists() and j.exists()):
                continue
            with self.subTest(ad=nome):
                m = MR.medir(v, str(j), 1.35)
                self.assertFalse(
                    MR.aprova(m)[0],
                    f"{nome} passou pelo criterio do gate com {m['cortes_min']:.1f}/min: "
                    f"o cruzamento plano x imagem afrouxou a calibragem")
                checados += 1
        if not checados:
            self.skipTest("nenhum ad lento com plano disponivel pra calibrar")

    def test_veredito_traz_o_motivo(self):
        m = {"cortes_min": 5.0, "plano_medio": 12.0, "maior_plano": 15.0, "cortes": 7,
             "dur": 90.0, "planos": [15.0]}
        ok, motivos = MR.aprova(m)
        self.assertFalse(ok)
        self.assertTrue(motivos, "reprovar sem dizer por que nao serve pra nada")

    def test_alvo_bate_com_a_referencia_medida(self):
        # o alvo nao pode ser um numero que eu inventei: tem que caber no que as
        # referencias entregam de fato
        self.assertLessEqual(MR.MIN_CORTES_MIN, 18.9,
                             "o piso esta acima da referencia mais lenta das tres")
        self.assertGreaterEqual(MR.MAX_PLANO_S, 3.17,
                                "o teto de plano esta abaixo do plano medio da ref mais lenta")


if __name__ == "__main__":
    unittest.main(verbosity=2)
