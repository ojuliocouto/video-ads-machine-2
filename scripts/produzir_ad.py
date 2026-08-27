#!/usr/bin/env python3
"""ÚNICO jeito autorizado de produzir um ad. Build e gate são um comando só.

GUARDRAIL (pedido do Julio, 05/08/2026): "voce precisa inserir algum tipo de guardrail
pra NUNCA trocar o metodo".

O que aconteceu: na leva OCC 08/2026 eu produzi AD03..AD12 chamando build_composite.py
direto, 10 seguidos, sem auditar nenhum, e so rodei auditoria depois de declarar pronto
e subir no Drive. Media 5,2, leva inteira reprovada. O ciclo build -> verificar ->
corrigir, que tinha produzido o AD01 e o AD02, foi exatamente o que eu descartei quando
o volume subiu.

Como este script impede a repeticao:
  1. build e gate acontecem na MESMA chamada: nao existe "buildei, depois vejo"
  2. se o gate reprova, o script sai com codigo 1 e NAO marca o ad como pronto
  3. em lote, um ad reprovado INTERROMPE a fila: nao da pra empilhar 10 defeituosos
  4. grava _status.json com o veredito, entao "esta pronto?" e uma leitura, nao memoria

Uso:
  python3 produzir_ad.py 03 espuma_roxa          # um ad
  python3 produzir_ad.py --lote                  # todos que tem config
  python3 produzir_ad.py --status                # so mostra o veredito atual
"""
import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from caminhos import V2L  # noqa: E402  (era o proprio dir; agora e o _local, que guarda o estado)
GATE = Path.home() / ".claude" / "scripts" / "gate-ad.py"
STATUS = V2L / "_status.json"

# rodizio: 3 avatares verticais, sem repetir entre vizinhos.
# O "dentro_carro" foi VETADO pelo Julio em 05/08/2026 ("nao ta bom"): enquadramento
# diagonal, ele de perfil com cinto, longe do padrao dos outros. Nao usar mais.
#
# 06 e 07 seguem o look do avatar HeyGen que JA foi gerado pra eles (estudio_verde e
# selfie_neon). Trocar so pra fechar uma tabela mais bonita custaria dois jobs novos de
# HeyGen (que recusa fila concorrente) sem ganho: a regra e nao repetir entre vizinhos,
# e essa sequencia ja nao repete.
LOOK_POR_AD = {
    "01": "espuma_roxa", "02": "estudio_verde", "03": "selfie_neon", "04": "estudio_verde",
    "05": "espuma_roxa", "06": "estudio_verde", "07": "selfie_neon", "08": "estudio_verde",
    "09": "espuma_roxa", "10": "estudio_verde", "11": "selfie_neon", "12": "espuma_roxa",
    # leva 2: PLANO MEDIO, porque o close deforma a boca no Avatar V (visto no AD13 em
    # 11/08/2026: labios e dentes borrados o video inteiro, o Julio pegou na hora).
    # So que "espuma_branca"/"estudio_claro"/"neon_branca" sao 16:9 com tarja, nao
    # servem em 9x16. Dos 9 looks, so 3 sao verticais nativos: #1 e #8 (close, boca
    # deformada) e #4 estudio_verde (plano medio). Entao a leva 2 inteira vai de
    # estudio_verde ate existir outro look vertical em plano medio.
    "13": "estudio_verde", "14": "estudio_verde", "15": "estudio_verde",
    "16": "estudio_verde", "17": "estudio_verde", "18": "estudio_verde",
    "19": "estudio_verde", "20": "estudio_verde", "21": "estudio_verde",
}


class FLock:
    """Trava entre processos, pra nunca existir dois builds ao mesmo tempo."""

    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self.f = None

    def __enter__(self):
        self.f = open(self.caminho, "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("   (outro build em andamento, aguardando a vez...)", flush=True)
            fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()
        return False


def ler_status():
    if STATUS.exists():
        try:
            return json.loads(STATUS.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def gravar_status(ad, veredito, detalhe=""):
    s = ler_status()
    s[ad] = {"veredito": veredito, "detalhe": detalhe}
    STATUS.write_text(json.dumps(s, ensure_ascii=False, indent=2))


def revisar_copy(ad):
    """Revisao de COPY antes do build: erro de transcricao vira legenda queimada.

    Ordem do Julio (05/08/2026). O parakeet ouve "Claude" e escreve "Cloud": foram 39
    ocorrencias em 10 dos 12 anuncios, o nome do produto errado na tela de um anuncio
    pago. Rodar ANTES do build, porque depois do render o erro custa 8 min por ad.
    """
    rev = Path.home() / ".claude" / "scripts" / "revisor-copy-ad.py"
    if not rev.exists():
        print("AVISO: revisor de copy ausente"); return True
    print(f"\n--- REVISAO DE COPY AD{ad} ---", flush=True)
    r = subprocess.run([sys.executable, str(rev), ad], capture_output=True, text=True)
    print(r.stdout, flush=True)
    return r.returncode == 0


# --------------------------------------------------------------------------
# GATE DE ENTRADA. Portado do vam_build.py, que era o builder da leva 1 (AD01-AD12).
#
# O QUE ACONTECEU (11/08/2026): na leva 2 eu troquei de builder e trouxe só os gates
# de SAÍDA (texto na tela, piscada, loudness, duração). Os três gates de ENTRADA do
# vam_build ficaram pra trás, e eles eram justamente os que pegavam este defeito:
#   G1 clean_existe        -> o áudio higienizado existe?
#   G1 sem_respiro_grande  -> sobrou pausa de respiração?
#   G2 avatar_usou_clean   -> o avatar foi gerado A PARTIR do áudio limpo?
#
# Sem eles, gerei o avatar do AD13 direto do .m4a cru do WhatsApp: 21% do arquivo era
# silêncio, com pausas de até 1,70s. O Avatar V tira a expressão do áudio, então em
# cada pausa longa ele INVENTA rosto, e é de onde vinham os sorrisos estranhos que o
# Júlio pegou. O gate `avatar_usou_clean` teria reprovado na hora: avatar 92,8s contra
# clean 78,0s.
#
# Lição igual à que já existe no topo deste arquivo: gate que depende de alguém
# lembrar de rodar não é gate. Tem que estar na MESMA chamada do build.
# --------------------------------------------------------------------------
from caminhos import V1, CODIGO  # noqa: E402
BIG_SIL_GATE = 0.62      # a partir daqui a pausa vale checar por respiro
RESPIRO_DB_GATE = -34.0  # energia DENTRO da pausa acima disso = respiro audivel
AVATAR_DUR_TOL = 1.0     # avatar e clean têm que casar em duração

# Look só entra se for vertical NATIVO e de plano MÉDIO. Os de plano fechado deformam
# lábios e dentes no Avatar V; os 16:9 entram com tarja e o rosto fica com 35% do quadro.
# ATUALIZADO 17/08/2026, por MEDIÇÃO, não por memória.
#
# A lista antiga tinha só "estudio_verde" e vetava os outros por dois motivos. Um deles
# caiu: "16:9 com tarja, nao serve em 9x16" está FACTUALMENTE ERRADO hoje. Medi os 8
# avatares gerados nesta leva com ffprobe e TODOS saíram 1080x1920 nativos, incluindo
# espuma_branca, estudio_claro e neon_branca. O Júlio já tinha dito ("tem varios
# verticais, so nao quero o do carro") e a medida confirma.
#
# O outro motivo (plano fechado borrando boca e dentes no Avatar V) era real quando
# apareceu, mas é qualidade de IMAGEM, não de proporção, e varia por geração. Então virou
# CONFERÊNCIA, não veto cego: antes de liberar um look de plano fechado, recortar a região
# da boca em resolução nativa e olhar. No jh13 espuma_roxa (17/08) a boca saiu com lábios
# definidos e dentes separados, sem borrão, e o Júlio autorizou o uso.
#
# Regra que o Júlio deu junto: "melhor gastar credito com um avatar novo do que ficar
# batendo cabeça". Boca deformada = regera o avatar naquele look, não bloqueia a leva.
# Medidos um a um por ffprobe: todos nativos 1080x1920. O `estudio_verde` saiu daqui em
# 17/08/2026: ele nao pertence ao grupo THALES LARAY OFICIAL, o unico autorizado pelo
# Julio. O `oficial_13` entrou no lugar dele.
LOOKS_OK_9X16 = {
    "oficial_13", "espuma_roxa", "espuma_branca", "estudio_claro",
    "neon_branca", "neon_creme", "espuma_fechado", "espuma_estampado",
}
LOOKS_VETADOS = {
    "dentro_carro": "vetado pelo Julio em 05/08/2026: enquadramento diagonal, de perfil",
}


def _dur(p):
    o = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True)
    try:
        return float(o.stdout.strip())
    except ValueError:
        return 0.0


def _respiros_grandes(audio, limite):
    """Pausas com RESPIRO AUDIVEL dentro. Nao pausa longa: pausa longa e ritmo.

    LOGICA INVERTIDA ATE 17/08/2026: isto devolvia toda pausa acima de `limite`, ou
    seja, tratava pausa natural de fim de frase como defeito. O efeito foi a
    higienizacao ser empurrada a achatar TUDO, e o Julio ouviu o resultado: "o audio
    foi todo picotado em varios momentos". No jh13 o bruto tinha 25 pausas de ate
    2,95s e o limpo saiu com ZERO acima de 0,55s.

    O defeito de verdade e respiro AUDIVEL dentro da pausa (o Avatar V lipsynca a
    respiracao e a boca mexe no vazio). Isso se mede por ENERGIA, nao por duracao.
    """
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(audio), "-af",
                        "silencedetect=noise=-30dB:d=0.28", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    inicios = [float(x) for x in re.findall(r"silence_start: ([\d.-]+)", r)]
    duracoes = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", r)]
    ruidosas = []
    for ini, dur_p in zip(inicios, duracoes):
        if dur_p < limite:
            continue
        a = subprocess.run(["ffmpeg", "-v", "info", "-ss", str(ini + 0.05),
                            "-t", str(min(dur_p - 0.1, 1.0)), "-i", str(audio),
                            "-af", "astats=metadata=1:reset=0", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
        vals = [float(x) for x in re.findall(r"RMS level dB:\s*(-?[\d.]+)", a)]
        if vals and max(vals) > RESPIRO_DB_GATE:
            ruidosas.append((round(ini, 1), round(dur_p, 2), round(max(vals), 1)))
    return ruidosas


def _ritmo_achatado(audio):
    """Pausa longa DEMAIS DE MENOS: fala sem respiracao de fim de frase soa picotada."""
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(audio), "-af",
                        "silencedetect=noise=-30dB:d=0.15", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    ps = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", r) if float(x) < 5]
    longas = [p for p in ps if p > 0.60]
    return len(longas), len(ps)


def gate_entrada(ad, look):
    """Confere áudio e avatar ANTES de montar. Reprova => o build nem começa."""
    print(f"\n--- GATE DE ENTRADA AD{ad} [{look}] ---", flush=True)
    falhas = []

    if look in LOOKS_VETADOS:
        falhas.append(f"look '{look}' vetado: {LOOKS_VETADOS[look]}")
    elif look not in LOOKS_OK_9X16:
        falhas.append(f"look '{look}' nao esta na lista aprovada pra 9x16 {sorted(LOOKS_OK_9X16)}")

    # PASTA DE AUDIO POR LEVA. Estava fixa em audios_leva2 e isso e uma bomba: os audios da
    # leva da Jheni tambem se chamam AD13..AD24 e sobrescreveriam os NOSSOS nove (que hoje
    # sao AD25..AD33 no Drive). Os dela moram em audios_leva4 e o ad vem prefixado com "jh".
    pasta = "audios_leva4" if str(ad).startswith("jh") else "audios_leva2"
    num = str(ad).replace("jh", "").replace("v2", "")
    limpo = V1 / "inputs" / pasta / f"AD{num}_higienizado.mp3"
    bruto = V1 / "inputs" / pasta / f"AD{num}.mp3"
    if not limpo.exists():
        if not bruto.exists():
            falhas.append(f"sem audio bruto em {bruto}")
        else:
            print(f"  higienizando {bruto.name} (passo obrigatorio, nao pulavel)...", flush=True)
            subprocess.run([sys.executable, str(CODIGO / "higienizar_audio.py"),
                            str(bruto), str(limpo)], capture_output=True, text=True)
    if limpo.exists():
        resid = _respiros_grandes(limpo, BIG_SIL_GATE)
        d_limpo = _dur(limpo)
        n_longas, n_pausas = _ritmo_achatado(limpo)
        print(f"  audio higienizado: {d_limpo:.1f}s | {len(resid)} pausa(s) com respiro "
              f"audivel | {n_longas} pausa(s) longa(s) de {n_pausas}")
        if resid:
            falhas.append(f"respiro audivel dentro de pausa: {resid}")
        # RE-TRANSCRICAO OBRIGATORIA (ordem do Julio, 17/08/2026): "transcrever NOVAMENTE
        # antes de jogar no avatar. esse processo precisa ser obrigatorio". O corte do
        # higienizador comeu 4 palavras no jh13 ("uma campanha"->"panha", "algo" apagado)
        # e so apareceu no video pronto, depois do avatar gerado.
        if bruto.exists() and os.environ.get("CHECAR_FALA") != "0":
            aa = CODIGO / "auditar_audio.py"
            if aa.exists():
                # (migracao 26/08/2026) codigo agora vizinho; import direto resolve
                try:
                    import auditar_audio
                    # (danos, suspeitas): so `danos` reprova. `suspeitas` sao
                    # divergencias de transcricao (elisao natural tipo "vamos embora"
                    # -> "vambora"), que aparecem no relatorio e nao travam a leva.
                    danos, suspeitas = auditar_audio.comparar_transcricoes(bruto, limpo)
                    print(f"  fala preservada: {'sim' if not danos else 'NAO'}"
                          + (f" -> {danos[:3]}" if danos else "")
                          + (f"  (transcricao divergiu em {len(suspeitas)}, sem dano)"
                             if suspeitas else ""), flush=True)
                    if danos:
                        falhas.append(
                            "corte comeu fala: " +
                            "; ".join(f"{o!r}->{n!r}" for o, n in danos[:4]))
                except Exception as e:
                    print(f"  [AVISO] checagem de fala falhou: {e}", flush=True)
        if d_limpo > 30 and n_longas < 4:
            falhas.append(
                f"ritmo achatado: so {n_longas} pausa(s) acima de 0,60s em {d_limpo:.0f}s. "
                "A fala fica sem respiracao de fim de frase e soa picotada. Rode "
                "auditar_audio.py, que higieniza preservando o ritmo.")
        # mesmo motivo da pasta de audio: os da Jheni tem prefixo "jh" pra nao colidir
        pref = "" if str(ad).startswith("jh") else "ad"
        avatar = V1 / "inputs" / f"{pref}{ad}v2_{look}_avatar.mp4"
        if not avatar.exists():
            falhas.append(f"avatar nao existe: {avatar.name}")
        else:
            d_av = _dur(avatar)
            print(f"  avatar: {d_av:.1f}s  |  clean: {d_limpo:.1f}s")
            if abs(d_av - d_limpo) > AVATAR_DUR_TOL:
                falhas.append(
                    f"avatar NAO foi gerado do audio limpo (avatar {d_av:.1f}s != "
                    f"clean {d_limpo:.1f}s). Regere com AD{ad}_higienizado.mp3")
    else:
        falhas.append("higienizacao nao produziu arquivo")

    for f in falhas:
        print(f"  REPROVA: {f}", flush=True)
    if not falhas:
        print("  >> PASSA", flush=True)
    return (not falhas), "; ".join(falhas)


def build(ad, look, fmt="9x16"):
    print(f"\n{'='*54}\nBUILD AD{ad} [{look}] {fmt}\n{'='*54}", flush=True)
    # UM build por vez, sempre. O motor v1 escreve em caminhos FIXOS (output/timing.json e
    # os segmentos de cena), entao dois builds simultaneos se sobrescrevem: rodei AD06 e
    # AD07 juntos e o 06 morreu em "invalid literal for int()" porque leu um segmento que
    # o 07 ja tinha trocado. Sozinho, o mesmo AD06 monta os 20 blocos sem um arranhao.
    with FLock(V2L / ".build.lock"):
        # terceiro e ultimo lugar onde o prefixo "ad" era fixo (os outros dois eram a
        # pasta de audio e o arquivo do avatar). Ad da leva da Jheni ja vem como "jh13".
        pref_cfg = "" if str(ad).startswith("jh") else "ad"
        r = subprocess.run([sys.executable, str(CODIGO / "build_composite.py"),
                            f"{pref_cfg}{ad}v2", look, fmt],
                           capture_output=True, text=True)
    saida = (r.stdout or "") + (r.stderr or "")
    print(saida[-600:], flush=True)
    return "OK ->" in saida


def gate(ad, fmt="9x16"):
    print(f"\n--- GATE AD{ad} {fmt} (obrigatorio, nao pulavel) ---", flush=True)
    if not GATE.exists():
        print(f"GATE AUSENTE em {GATE}. Sem gate nao ha entrega.", flush=True)
        return False, "gate ausente"
    env = dict(os.environ, GATE_FMT=fmt)
    r = subprocess.run([sys.executable, str(GATE), ad], capture_output=True, text=True, env=env)
    print(r.stdout, flush=True)
    motivos = [l.strip(" -") for l in r.stdout.split("\n") if l.strip().startswith("- ")]
    ok = r.returncode == 0

    # COLISAO DE TEXTO COM O ROSTO (17/08/2026): o Julio mandou quatro prints de um ad
    # ja "aprovado" pelos outros gates, com legenda na boca dele e CTA atravessado na
    # cara. Nenhum gate media isso, entao so o olho humano pegava, e depois de pronto.
    # Agora e medido: rosto por Haar, tinta por alpha do overlay, e reprova antes de
    # entregar. Ver ~/.claude/scripts/gate-colisao-texto.py.
    # RITMO (26/08/2026): o `medir_ritmo.py` existe, funciona, retorna exit 1 e
    # REPROVAVA o jh13 entregue (63% do anuncio em plano acima de 6s, teto 40%). E
    # ninguem o chamava no build: `grep -rn medir_ritmo produzir_ad.py
    # build_composite.py` nao devolvia nada. O ad saiu carimbado PRONTO no _status.json
    # com o gate de ritmo reprovando, porque o gate estava desligado da tomada.
    # Regra da casa: checklist nao bloqueia, GATE bloqueia.
    if os.environ.get("GATE_RITMO") != "0":
        _pref = "" if str(ad).startswith("jh") else "ad"
        _fin = sorted(V1.glob(f"output/{_pref}{ad}v2_*_v2composite_{fmt}.mp4"),
                      key=lambda q: q.stat().st_mtime)
        if _fin:
            # O PLANO VAI JUNTO (27/08/2026). Sem ele o medidor cai na deteccao de
            # cena, que compara diferenca ABSOLUTA de pixel e erra dos dois lados no
            # mesmo arquivo: contou 10 "cortes" num fundo desfocado piscando e depois
            # deixou de ver `orig -> cheio`, que troca 100% do conteudo, porque o
            # anuncio e escuro de ponta a ponta. Com o plano, o corte precisa ser PEDIDO
            # por nos E VISTO na imagem. Ver cortes_confirmados() no medir_ritmo.
            _cmd = [sys.executable, str(CODIGO / "medir_ritmo.py"), str(_fin[-1])]
            _rj = sorted(V1.glob(f"output/{_pref}{ad}v2_*_footage_1x_ritmo.json"),
                         key=lambda q: q.stat().st_mtime)
            if _rj:
                from build_composite import ACCEL as _acc2
                _cmd += ["--ritmo-json", str(_rj[-1]), "--accel", str(_acc2)]
            _rr = subprocess.run(_cmd, capture_output=True, text=True)
            print(_rr.stdout, flush=True)
            if _rr.returncode != 0:
                ok = False
                _ls = [l.strip() for l in _rr.stdout.split("\n") if "REPROVA" in l]
                motivos.append("ritmo: " + "; ".join(_ls[:2])[:180])

    gcol = Path.home() / ".claude" / "scripts" / "gate-colisao-texto.py"
    if gcol.exists() and os.environ.get("GATE_COLISAO") != "0":
        pref = "" if str(ad).startswith("jh") else "ad"
        finais = sorted(V1.glob(f"output/{pref}{ad}v2_*_v2composite_{fmt}.mp4"),
                        key=lambda p: p.stat().st_mtime)
        ovl = sorted((V2L).glob(f"render-{pref}{ad}v2-*-ovlonly/renders/*.mov"),
                     key=lambda p: p.stat().st_mtime)
        if finais:
            cmd = [sys.executable, str(gcol), str(finais[-1])]
            if ovl:
                cmd += ["--overlay", str(ovl[-1])]
            # O PLANO DE RITMO VAI JUNTO (27/08/2026). Sem ele o gate nao sabe quando o
            # anuncio esta em tela dividida, e a caixa de rosto HERDADA de um plano de
            # tela cheia continua valendo no painel do insert. Foi assim que o jh13
            # reprovou com o lettering "colidindo" com uma ampulheta de areia ambar.
            _rit = sorted(V1.glob(f"output/{pref}{ad}v2_*_footage_1x_ritmo.json"),
                          key=lambda p: p.stat().st_mtime)
            if _rit:
                from build_composite import ACCEL as _acc
                cmd += ["--ritmo", str(_rit[-1]), "--accel", str(_acc)]
            rc = subprocess.run(cmd, capture_output=True, text=True)
            print(rc.stdout, flush=True)
            if rc.returncode != 0:
                ok = False
                extras = [l.strip() for l in rc.stdout.split("\n") if "texto cobre" in l]
                motivos.append("colisao texto x rosto: " + "; ".join(extras[:3]))
    return ok, "; ".join(motivos)


def produzir(ad, look=None, fmt="9x16"):
    look = look or LOOK_POR_AD.get(ad)
    if not look:
        print(f"AD{ad}: look nao definido"); return False
    # 9x16 e 1x1 tem veredito proprio no _status.json: a chave do 1x1 leva sufixo pra um
    # nao apagar o resultado do outro.
    chave = ad if fmt == "9x16" else f"{ad}:1x1"
    # ESCAPE USADO FICA GRAVADO. Antes um bypass sumia no terminal e o _status.json
    # dizia PRONTO como se tudo tivesse sido verificado: "esta pronto?" voltava a ser
    # memoria em vez de leitura. Agora o veredito carrega a marca do bypass.
    bypass = [v for v in ("FASE_GATE", "FIDELIDADE", "FASE_GATE_LEGADO")
              if os.environ.get(v) in ("0", "1") and
              ((v == "FASE_GATE_LEGADO" and os.environ.get(v) == "1") or
               (v != "FASE_GATE_LEGADO" and os.environ.get(v) == "0"))]
    if bypass:
        print(f"\n!!! AD{ad}: BYPASS ATIVO -> {bypass}. So use com ordem explicita do "
              "Julio; isso vai gravado no _status.json.", flush=True)
    # GATE DE FASES (17/08/2026): fase0 ingestao + fase1 avatares em lote + fase2 plano
    # de edicao APROVADO pelo Julio, tudo com evidencia, ANTES de qualquer build.
    # Desligar so com ordem explicita dele: FASE_GATE=0.
    if os.environ.get("FASE_GATE") != "0":
        fg = subprocess.run([sys.executable, str(CODIGO / "fase_gate.py"), "check-build", ad],
                            capture_output=True, text=True)
        if fg.returncode != 0:
            motivo = (fg.stdout + fg.stderr).strip()
            gravar_status(chave, "FASES PENDENTES", motivo)
            print(f"\n>>> AD{ad} {fmt}: {motivo}", flush=True)
            return False
    # GATE DE FIDELIDADE AO DOC (ordem do Julio, 17/08/2026: "seguir FIELMENTE o
    # que tem nos comentarios e direcionamentos do doc"). Confere insert por insert
    # contra o que a Brigida marcou, e a estrutura de blocos. Gap legitimo vai
    # declarado em _fidelidade_excecoes.json com motivo escrito.
    # So roda pra ad que tem fonte declarada em _doc_map.json.
    if os.environ.get("FIDELIDADE") != "0":
        mapa_p = V2L / "_doc_map.json"
        mapa = json.loads(mapa_p.read_text()) if mapa_p.exists() else {}
        if ad not in mapa:
            # SEM FONTE DECLARADA O BUILD NAO ANDA. Antes o gate so rodava pra ad
            # presente no mapa e PULAVA em silencio pros outros, que e exatamente a
            # etapa pulada que o Julio proibiu (17/08/2026): jh14..jh21 tem roteiro
            # pronto e nenhum tinha fonte declarada.
            motivo = (f"ad '{ad}' nao tem fonte declarada em _doc_map.json "
                      "(doc, aba, secao). Sem isso nao da pra verificar fidelidade "
                      "ao doc, e pular a verificacao nao e opcao.")
            gravar_status(chave, "SEM FONTE NO DOC", motivo)
            print(f"\n>>> AD{ad} {fmt}: {motivo}", flush=True)
            return False
        vf = subprocess.run([sys.executable, str(CODIGO / "verificar_fidelidade.py"), ad],
                            capture_output=True, text=True)
        print(vf.stdout, flush=True)
        if vf.returncode != 0:
            motivos = [l.strip(" -") for l in vf.stdout.split("\n")
                       if l.strip().startswith("- ")]
            gravar_status(chave, "INFIEL AO DOC", "; ".join(motivos))
            print(f"\n>>> AD{ad} {fmt}: INFIEL AO DOC, build nem comecou", flush=True)
            return False
    if not revisar_copy(ad):
        gravar_status(chave, "COPY COM ERRO",
                      "erro de transcricao no roteiro; rode revisor-copy-ad.py --corrigir")
        print(f"\n>>> AD{ad} {fmt}: COPY COM ERRO, build nem comecou", flush=True)
        return False
    ok_entrada, motivo = gate_entrada(ad, look)
    if not ok_entrada:
        gravar_status(chave, "ENTRADA REPROVADA", motivo)
        print(f"\n>>> AD{ad} {fmt}: ENTRADA REPROVADA, build nem comecou", flush=True)
        return False
    if not build(ad, look, fmt):
        gravar_status(chave, "BUILD FALHOU")
        return False
    ok, motivos = gate(ad, fmt)
    veredito = "PRONTO" if ok else "REPROVADO"
    if bypass:
        veredito += " (COM BYPASS)"
        motivos = f"BYPASS: {','.join(bypass)}; " + (motivos or "")
    gravar_status(chave, veredito, motivos)
    print(f"\n>>> AD{ad} {fmt}: {'PRONTO' if ok else 'REPROVADO NO GATE'}", flush=True)
    return ok


def main():
    if "--status" in sys.argv:
        s = ler_status()
        if not s:
            print("nenhum ad processado ainda"); return
        for ad in sorted(s):
            v = s[ad]
            print(f"  AD{ad}: {v['veredito']:12s} {v.get('detalhe','')[:80]}")
        pend = [a for a, v in s.items() if v["veredito"] != "PRONTO"]
        print(f"\n{len(s)-len(pend)} prontos, {len(pend)} pendentes")
        sys.exit(1 if pend else 0)

    if "--lote" in sys.argv:
        alvos = [a for a in sorted(LOOK_POR_AD)
                 if (V2L / "configs").glob(f"ad{a}v2_*.json")]
        for ad in alvos:
            if not produzir(ad):
                # um reprovado PARA a fila: nao empilha defeito
                print(f"\nFILA INTERROMPIDA no AD{ad}. Corrija antes de seguir.")
                sys.exit(1)
        print("\nlote inteiro passou no gate")
        return

    fmt = "9x16"
    argv = sys.argv[1:]
    if "--fmt" in argv:
        i = argv.index("--fmt")
        fmt = argv[i + 1]
        del argv[i:i + 2]
    if not argv:
        sys.exit(__doc__)
    ad = argv[0].zfill(2)
    look = argv[1] if len(argv) > 1 else None
    sys.exit(0 if produzir(ad, look, fmt) else 1)


if __name__ == "__main__":
    main()
