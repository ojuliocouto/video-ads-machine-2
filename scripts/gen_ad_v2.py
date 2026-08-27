"""Gerador genérico de anúncio no motor v2 (HyperFrames), padrão validado no piloto ad2.

Fonte de verdade por ad: o roteiro anotado do motor v1 (inputs/adXX_leva.txt, verbatim ao
falado) + o mapa de inserts do v1 (inputs/adXX_inserts.json). Este script deriva TUDO deles:
spans por bloco (contíguos), b-roll cards, captions word-by-word (kw por frase), letterings,
CTA/logo no bloco final, grid-wipes nas fronteiras de grupos de b-roll.

Uso: python3 gen_ad_v2.py <config.json>
Config: {"ad","look","avatar","out_dir","hook":{eyebrow,l1,accent},"cta_label",
         "kw_phrases":[...],"letterings":[{"lead","key","anchor","nth":1,"dur":2.2}]}
"""
import json
import math
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

from caminhos import V1  # noqa: E402
from caminhos import V2  # noqa: E402
def _sem_emoji(t):
    """Remove pictogramas do texto de tela (19/08/2026, Jheni: "esses emojis deixam
    ainda mais com cara de pobre"). A COPY do doc nao muda: o strip e so na
    renderizacao do overlay (hook, lettering, chip)."""
    out = []
    for ch in str(t):
        if ord(ch) >= 0x1F000 or (0x2600 <= ord(ch) <= 0x27BF) or ch in "\u2b50\ufe0f\u200d":
            continue
        out.append(ch)
    return " ".join("".join(out).split())


TEMPLATE = V2 / "templates" / "reel-editorial" / "index.html"
HF = str(V2 / "node_modules" / ".bin" / "hyperframes")

# (migracao 26/08/2026) codigo agora vizinho; import direto resolve
# (migracao 26/08/2026) codigo agora vizinho; import direto resolve
import build_timeline
from parser_roteiro import parse as parse_v1

SPEED = 1.15         # aceleracao global do video (v1 fazia 1.2x); pre-acelera o avatar
HOOK_END = round(2.5 / SPEED, 3)   # 2.174s: hook encolhe junto com a fala acelerada
XFADE_GAP = 0.6      # brolls a menos de isso um do outro = mesmo grupo (sem wipe entre eles)
TAIL_PAD = 0.65      # folga de cauda: garante root/audio >= duracao real do audio (sem corte)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"ERRO: {' '.join(str(c) for c in cmd)}\n{r.stderr[-800:]}")
    return r


def vdur(f):
    o = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(f)], capture_output=True, text=True).stdout.strip()
    return float(o) if o else 0.0


def norm(w):
    w = unicodedata.normalize("NFKD", w)
    w = "".join(c for c in w if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", w.lower())


def main(cfg_path):
    cfg = json.loads(Path(cfg_path).read_text())
    ad, look = cfg["ad"], cfg["look"]
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True, exist_ok=True)

    # template por formato: 9x16 (padrao) ou 1x1 (quadrado dedicado)
    fmt = cfg.get("format", "9x16")
    tmpl = V2 / "templates" / ("reel-editorial-1x1" if fmt == "1x1" else "reel-editorial") / "index.html"

    # ---------- assets base ----------
    # Aceleracao (D6): pre-acelera o avatar (video setpts + audio atempo, pitch preservado)
    # ANTES de transcrever, entao TUDO (spans, brolls, legendas, letterings, wipes, CTA)
    # nasce em tempo 1.15x sozinho. Nao regera avatar no HeyGen: e so re-timing local.
    avatar_src = Path(cfg["avatar"])
    speed = float(cfg.get("speed", SPEED))
    dst = out / "avatar.mp4"
    target = round(vdur(avatar_src) / speed, 2)
    if not dst.exists() or abs(vdur(dst) - target) > 0.05:
        # ao (re)acelerar, invalida derivados que dependem do avatar (transcript + brolls)
        for old in [out / "transcript.json", *out.glob("broll*.mp4")]:
            old.unlink(missing_ok=True)
        if abs(speed - 1.0) < 1e-3:
            shutil.copy(avatar_src, dst)
        else:
            run(["ffmpeg", "-y", "-i", str(avatar_src),
                 "-filter_complex", f"[0:v]setpts=PTS/{speed}[v];[0:a]atempo={speed}[a]",
                 "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
                 "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                 "-c:a", "aac", "-b:a", "192k", str(dst)])
    _real = vdur(dst)
    total = math.ceil((_real + TAIL_PAD) * 100) / 100   # D5: cauda nunca cortada
    if not (out / "fonts").exists():
        shutil.copytree(tmpl.parent / "fonts", out / "fonts")
    shutil.copy(V2 / "_local" / "render-reel-editorial" / "logo_occ_wordmark.png", out / "logo.png")
    shutil.copy(V2 / "_local" / "render-reel-editorial" / "meta.json", out / "meta.json")

    # ---------- transcript ----------
    if not (out / "transcript.json").exists():
        run([HF, "transcribe", "avatar.mp4", "--engine", "parakeet", "--json", "-d", "."], cwd=out)
    transcript = json.loads((out / "transcript.json").read_text())

    # ---------- roteiro v1: blocos + narracao ----------
    blocks = parse_v1(str(V1 / "inputs" / f"{ad}_leva.txt"))
    inserts_map = json.loads((V1 / "inputs" / f"{ad}_inserts.json").read_text())
    narr_words = []
    for b in blocks:
        narr_words += b["narr"].split()
    words = build_timeline.align_words(narr_words, transcript)

    # kw por frase (autoria por ad)
    joined = " ".join(w["text"] for w in words)
    for phrase in cfg.get("kw_phrases", []):
        p_norm = [norm(t) for t in phrase.split()]
        w_norm = [norm(w["text"]) for w in words]
        for i in range(len(w_norm) - len(p_norm) + 1):
            if w_norm[i:i + len(p_norm)] == p_norm:
                for k in range(len(p_norm)):
                    words[i + k]["kw"] = True

    # spans por bloco (CONTIGUOS: fronteira = 1a palavra do proximo bloco)
    idx = 0
    starts = []
    for b in blocks:
        n = len(b["narr"].split())
        starts.append(words[idx]["start"] if n else (words[idx - 1]["end"] if idx else 0.0))
        idx += n
    bounds = [starts[0]]
    for s in starts[1:]:
        bounds.append(max(s, bounds[-1] + 0.3))
    bounds.append(max(words[-1]["end"], bounds[-1] + 0.3))
    spans = [(bounds[i], bounds[i + 1]) for i in range(len(blocks))]

    # ---------- timing do hook (fica ~4s na tela: pedido do Julio) ----------
    # Opening em insert: o hook cobre o insert inteiro e dissolve no retorno do avatar.
    # Opening no avatar: segura o hook ~4s por cima. Minimo 4s de tela sempre.
    opening_insert = blocks[0]["type"] == "insert" and len(spans) > 1
    # o hook cobre o OPENING INSERT inteiro e dissolve no RETORNO DO AVATAR (sem invadir o
    # rosto). Retorno do avatar = primeiro bloco NAO-insert, nao spans[1] (spans[1] so e o
    # avatar quando o bloco 1 ja e avatar). Com 2+ inserts de abertura seguidos (ad03/ad04),
    # usar spans[1] fazia o hook sumir cedo demais e deixava zona morta (hook foi, rosto
    # ainda coberto por b-roll) = sensacao de corte. Segurar ate o rosto voltar mata isso.
    first_avatar_i = next((i for i, b in enumerate(blocks) if b["type"] != "insert"), 1)
    ref = spans[first_avatar_i][0] if opening_insert and first_avatar_i < len(spans) else (spans[1][0] if len(spans) > 1 else spans[0][1])
    # ...mas com TETO. Com 3 inserts de abertura seguidos (ad02v2) "segurar ate o rosto
    # voltar" deu 15.5s de hook congelado: ele tapava justamente o payoff dos inserts E,
    # como o cap_gate acompanha o hook, o anuncio ficava 15s SEM LEGENDA NENHUMA (fatal
    # em Reels com som desligado). O pedido original do Julio era hook de ~4s; o teto
    # restaura isso sem quebrar o caso de 1 insert curto, onde ref ja cai antes de 5s.
    # 5.0s deixava 4s sem legenda na abertura (o gate reprova acima de 2.5s de vao).
    # 3.2s mantem o gancho legivel e libera a legenda quase junto com a fala.
    HOOK_MAX = 3.2
    hook_gone = round(min(ref + 0.1, HOOK_MAX), 2) if opening_insert else 3.0
    hook_fade = round(hook_gone - 0.4, 2)   # comeca a dissolver
    hook_dur = round(hook_gone + 0.1, 2)    # janela do clip cobre ate depois do fade
    # sem legenda enquanto o hook esta na tela
    cap_gate = round(hook_gone - 0.05, 2) if opening_insert else hook_gone

    # ---------- brolls (blocos insert) ----------
    def find_insert_cfg(instr):
        s = instr.lower()
        for k, v in inserts_map.items():
            if k in s:
                return k, v
        return None, None

    brolls = []
    # Janelas por TIPO de insert, pra deconflitar o texto de tela mais adiante. Sem isso o
    # overlay posiciona lettering e legenda como se todo bloco fosse avatar em tela cheia,
    # e ai eles caem em cima do conteudo do insert. Dois casos reais medidos nesta leva:
    #   split   AD21: o lettering "SKILLS DO ZERO" caiu no rosto do Thales, porque no split
    #           ele mora na metade de baixo e o lettering e centrado no quadro inteiro.
    #   texto   AD15 aos 36,8s: a legenda "vendendo" caiu em cima do paragrafo do card de
    #           depoimento do Marco Aurelio, e os dois ficaram dificeis de ler.
    # PLANO DE RITMO, o mesmo que a footage usa (ritmo.py). Aqui ele serve pra saber em
    # que instante a imagem esta em insert e em que instante ela volta pro avatar: sem
    # isso o texto e posicionado como se o bloco inteiro fosse insert.
    # (migracao 26/08/2026) codigo agora vizinho; import direto resolve
    import ritmo as _R
    _entrada_ritmo = []
    for _i, (_b, (_s, _e)) in enumerate(zip(blocks, spans)):
        _k, _c = find_insert_cfg(_b["instr"]) if _b["type"] == "insert" else (None, None)
        _entrada_ritmo.append({"tipo": "insert" if _b["type"] == "insert" else "orig",
                               "s": _s, "e": _e,
                               "crop": (_c or {}).get("crop"),
                               "dur_max": (_c or {}).get("dur_max")})
    _plano_ritmo = _R.plano_de_ritmo(_entrada_ritmo)
    _res_ritmo = _R.resumo(_plano_ritmo, spans[-1][1])
    print(f"   [ritmo] {len(_plano_ritmo)} planos | {_res_ritmo['cortes_min']:.1f} "
          f"cortes/min | plano medio {_res_ritmo['plano_medio']:.2f}s", flush=True)
    # trechos em que a imagem NAO esta no insert, ainda que o bloco seja de insert
    _trechos_avatar = [(x["s"], x["e"]) for x in _plano_ritmo if x["tipo"] != "insert"]

    janelas_split, janelas_texto = [], []
    # instante em que a imagem VOLTA pro avatar por causa de um cap de insert;
    # e onde o CTA sobe no fim do anuncio (ver bloco do cta_start).
    _retorno_avatar = []
    for i, (b, (s, e)) in enumerate(zip(blocks, spans)):
        if b["type"] != "insert":
            continue
        key, icfg = find_insert_cfg(b["instr"])
        if not icfg:
            sys.exit(f"bloco insert {i} sem key no inserts.json: {b['instr']}")
        s2 = 0.0 if i == 0 else s   # opening: o insert e o fundo do opening desde t=0, sob o hook (sem avatar)
        dur = e - s2
        # CAP DE DURACAO (18/08/2026): b-roll longo no fim empurra o CTA pra 1,98s de
        # vida (reprovacao do diretor de arte no jh13: bloco 15 com 11,5s de insert e o
        # CTA nascendo so depois dele). Com dur_max o insert entrega o que o doc pede e a
        # imagem volta pro Thales antes do fim, que e onde o CTA sobe.
        if icfg.get('dur_max'):
            cap_d = float(icfg['dur_max'])
            if cap_d < dur:
                # ESPALHAMENTO (18/08/2026): o cap nao corta mais o bloco num ponto so.
                # O ritmo.py espalha o orcamento de tela em fatias pelo bloco INTEIRO,
                # entao a "volta pro avatar" que interessa (onde o CTA sobe) e o inicio
                # do ULTIMO plano de rosto do bloco, nao s2+cap. E as janelas de texto
                # NAO podem ser cortadas em s2+cap, senao invertem (inicio > fim).
                _rostos = [x for x in _plano_ritmo
                           if x["bloco"] == i and x["tipo"] != "insert"]
                _volta = _rostos[-1]["s"] if _rostos else s2 + cap_d
                print(f"   [cap] insert '{key}': {cap_d:.2f}s de tela espalhados em "
                      f"{dur:.2f}s de bloco; ultima volta pro avatar em {_volta:.2f}s",
                      flush=True)
                _retorno_avatar.append((i, round(_volta, 2)))
        if dur < 0.6:
            continue
        # so os PLANOS que continuam sendo insert entram na janela; o trecho que o
        # ritmo devolveu pro avatar tem que ser tratado como avatar, senao o lettering
        # daquele trecho desce pro peito achando que tem insert em cima dele.
        _meus = [(x["s"], x["e"]) for x in _plano_ritmo
                 if x["bloco"] == i and x["tipo"] == "insert"]
        # LAYOUT POR FATIA, NAO POR BLOCO (27/08/2026, regra dos dois motores).
        # O ritmo alterna as visitas do mesmo asset entre `split` (com o Thales embaixo)
        # e `cheio` (card grande, sem Thales). A footage ja obedecia isso; aqui a legenda
        # ainda decidia pelo `split` do CONFIG, que vale pro bloco inteiro. Resultado: a
        # fatia `cheio` recebia a classe da COSTURA, calibrada pra emenda entre paineis
        # que naquela fatia nao existe. E o mesmo desencontro do `dur_max` de 18/08 (CTA
        # em cima da boca): regra de tempo/posicao nova tem que entrar nos DOIS motores.
        _cheias = {(round(x["s"], 2), round(x["e"], 2)) for x in _plano_ritmo
                   if x["bloco"] == i and x["tipo"] == "insert"
                   and x.get("layout") == "cheio"}
        if not _meus:
            _meus = [(s2, s2 + dur)]
        if icfg.get("split"):
            for _a, _b2 in _meus:
                _par = (round(_a, 2), round(min(_b2, e), 2))
                # fatia sem o apresentador embaixo se comporta como insert de tela cheia
                (janelas_texto if (round(_a, 2), round(_b2, 2)) in _cheias
                 else janelas_split).append(_par)
        else:
            # INSERT EM TELA CHEIA: a posicao padrao da legenda (y~1375) e calibrada pro
            # AVATAR, onde cai no peito. Sobre um insert ela cai no MEIO do conteudo: o
            # Julio mandou print do diagrama do ciclo com a palavra "quando" tapando o
            # card 3. Insert existe pra ser visto, entao a legenda desce pro rodape.
            # Regra por TIPO DE FUNDO, nao por asset: avatar -> padrao, insert -> baixa,
            # split -> costura. Antes so `texto_proprio` (marcado a mao, asset por asset)
            # descia, e ninguem tinha marcado esses.
            for _a, _b2 in _meus:
                janelas_texto.append((round(_a, 2), round(min(_b2, e), 2)))
        if icfg.get("texto_proprio"):
            janelas_texto.append((round(s2, 2), round(s2 + dur, 2)))
        label = cfg.get("labels", {}).get(key, key)
        brolls.append({"src_file": icfg["file"], "start": round(icfg.get("start", 0), 2),
                       "s": round(s2, 2), "d": round(dur, 2), "label": label})

    # preparar arquivos de broll (h264 mp4, trim no start do insert, loop se curto)
    IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
    for k, b in enumerate(brolls):
        name = f"broll{k+1:02d}.mp4"
        dst = out / name
        need = b["d"] + 0.6
        # dst.stat().st_size == 0: guarda contra cache de um broll de tentativa anterior que
        # ficou truncado (ex: processo morto no meio do encode). Sem isso, "if not dst.exists()"
        # reusa silenciosamente um arquivo de 0 bytes pra sempre.
        if not dst.exists() or dst.stat().st_size == 0:
            is_img = str(b["src_file"]).lower().endswith(IMG_EXTS)
            cmd = ["ffmpeg", "-y"]
            if is_img:
                # imagem estatica: -stream_loop -1 no demuxer image2 nao respeita -t de forma
                # confiavel (fica rodando indefinidamente). -loop 1 e o jeito certo de repetir
                # um frame unico por -t segundos. Ken Burns leve pra nao ficar uma imagem morta.
                dur = round(need + 0.5, 2)
                zoom = f"zoompan=z='min(zoom+0.0007,1.06)':d={int(dur*30)}:s=1080x1920:fps=30"
                cmd += ["-loop", "1", "-i", b["src_file"], "-t", str(dur),
                        "-vf", zoom,
                        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", "-an", str(dst)]
            else:
                src_d = vdur(b["src_file"])
                avail = src_d - b["start"]
                if avail < need:  # loop
                    cmd += ["-stream_loop", "-1"]
                # keyframes densos (seek rapido) + faststart. O black-hole do wipe (~0.3-0.5s de
                # tela preta na entrada de um insert 100% SDR) SO some com o modo de captura
                # "layered/screenshot" do HyperFrames (via -pix_fmt yuv420p10le + metadata HDR no
                # broll, ver git log). Esse modo e MUITO mais pesado em CPU/RAM e estourou a
                # memoria da maquina de producao mesmo com 1 worker/low-memory-mode. Testado e
                # descartado como fix no modo leve "beginframe": keyframes (g=1, ate arquivo cru
                # sem re-encode), workers=1, --experimental-fast-capture=false, --hdr, preload
                # antecipado do <video> (1.5 a 4s, com opacity 0/0.01/1+cobridor separado), nenhum
                # resolve, o modo rapido tem uma limitacao propria com <video> nesse cenario. Ver
                # LEARNINGS.md gotcha #15.
                cmd += ["-ss", str(b["start"]), "-t", str(need + 0.5), "-i", b["src_file"],
                        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-vsync", "cfr", "-r", "30",
                        "-g", "15", "-keyint_min", "15", "-sc_threshold", "0",
                        "-force_key_frames", "expr:eq(n,0)",
                        "-movflags", "+faststart", "-an", str(dst)]
            run(cmd)
        b["src"] = name

    # ---------- letterings (autoria por ad: ancora = n-esima ocorrencia de palavra) ----------
    # Calculados ANTES das legendas pra deconflitar: o lettering vira o texto
    # principal do trecho, entao a legenda word-by-word e suprimida na janela dele
    # (senao a mesma frase aparece 2x na tela: lettering no peito + legenda no rodape).
    letts = []
    for L in cfg.get("letterings", []):
        alvo, nth, n = norm(L["anchor"]), L.get("nth", 1), 0
        t0 = None
        for w in words:
            if norm(w["text"]) == alvo:
                n += 1
                if n == nth:
                    t0 = w["start"]
                    break
        if t0 is None:
            sys.exit(f"lettering sem ancora: {L}")
        dur_l = L.get("dur", 2.2)
        # lettering que cai DENTRO de um bloco split desce pro peito do Thales. No
        # split ele mora na metade de baixo do quadro, e o lettering centrado pousa
        # exatamente no rosto dele (medido no AD21: "SKILLS DO ZERO" em cima da cara).
        no_split = any(a <= t0 < b for a, b in janelas_split)
        # LOGO DA OCC junto do lettering, quando o roteiro pede (bloco marcado com
        # "+ logo da occ"). O diretor de arte reprovou o jh13 porque o bloco 14 pede
        # lettering E logo e a tela nao mostrava nenhum dos dois. O lettering ja e um
        # elemento com data-start/data-duration animado por CSS+GSAP, entao o logo entra
        # DENTRO dele: mesma janela, mesma animacao, zero timing novo pra desencontrar.
        _bloco_do_lett = next((bi for bi, (bs, be) in enumerate(spans)
                               if bs <= t0 < be), None)
        _tem_logo = (_bloco_do_lett is not None
                     and "logo" in blocks[_bloco_do_lett]["instr"].lower())
        if _tem_logo:
            print(f"   [logo] roteiro pede logo no bloco {_bloco_do_lett}: entra junto "
                  f"do lettering em {t0:.2f}s", flush=True)
        letts.append({"id": f"lett{chr(65+len(letts))}", "lead": L["lead"], "key": L["key"],
                      "start": round(t0, 2), "dur": dur_l, "split": no_split,
                      "logo": _tem_logo, "baixo": bool(L.get("baixo")),
                      "pilha": L.get("pilha")})
    # PILHA: letterings que declaram o mesmo `pilha` viram UM bloco com varias linhas.
    # Cada linha entra na hora da propria palavra (delay relativo ao inicio do grupo) e
    # NENHUMA sai antes do fim: e o quadro de recompensa da lista.
    _grupos, _ordem = {}, []
    for l in letts:
        g = l.get("pilha")
        if not g:
            _ordem.append(l); continue
        if g not in _grupos:
            _grupos[g] = l
            l["linhas"] = []
            _ordem.append(l)
        base = _grupos[g]
        base["linhas"].append({"key": l["key"], "delay": round(l["start"] - base["start"], 2)})
        base["dur"] = round(max(base["start"] + base["dur"],
                                l["start"] + l["dur"]) - base["start"], 2)
    letts = _ordem
    # NAO ATRAVESSAR TROCA DE LAYOUT (27/08/2026, segunda passada, DEPOIS da fusao).
    # A primeira versao rodava dentro do laco de letterings, e a fusao da pilha logo
    # abaixo recalculava `base["dur"]` a partir da ultima linha, apagando o corte. O
    # resultado foi o `lettC` ("ENQUANTO ISSO") ficar 1,7s em cima da cara do Thales
    # (t=60,0s a 61,7s, 9,2% a 13,1% do nucleo do rosto), e o gate nao pegou porque o
    # proprio texto em cima da cara quebra o detector: 1,27s sem NENHUMA deteccao,
    # justamente ali. Ponto cego que a referencia ja documentava.
    # Aqui a trava roda uma vez so, no bloco que de fato vai pra tela.
    for l in letts:
        for _a, _b in janelas_split:
            _borda = (_a if l["start"] < _a < l["start"] + l["dur"]
                      else (_b if l["start"] < _b < l["start"] + l["dur"] else None))
            if _borda is None:
                continue
            _novo = round(_borda - l["start"] - 0.10, 2)
            if _novo >= 1.20:
                print(f"   [layout] lettering '{l['key'][:26]}' encurtado de "
                      f"{l['dur']:.2f}s pra {_novo:.2f}s: o quadro troca de layout em "
                      f"{_borda:.2f}s", flush=True)
                l["dur"] = _novo
            else:
                _novo_t0 = round(_borda + 0.10, 2)
                print(f"   [layout] lettering '{l['key'][:26]}' adiado de {l['start']:.2f}s "
                      f"pra {_novo_t0:.2f}s: nao cabe antes da troca", flush=True)
                l["dur"] = round(l["dur"] - (_novo_t0 - l["start"]), 2)
                l["start"] = _novo_t0
                if l.get("linhas"):
                    l["linhas"] = [{**x, "delay": max(0.0, round(x["delay"]
                                    - (_novo_t0 - l["start"]), 2))} for x in l["linhas"]]
            l["split"] = any(x <= l["start"] < y for x, y in janelas_split)
            break
    for l in letts:
        if l.get("linhas"):
            print(f"   [pilha] {l['id']}: {len(l['linhas'])} linhas, "
                  f"{l['start']:.2f}s por {l['dur']:.2f}s "
                  f"(delays {[x['delay'] for x in l['linhas']]})", flush=True)
    lett_windows = [(l["start"], l["start"] + l["dur"]) for l in letts]
    for i, l in enumerate(letts):
        l["id"] = f"lett{chr(65 + i)}"
    # rastro de deconflito: sem isso nao da pra saber se a flag chegou (perdi um build
    # inteiro achando que o CSS estava errado quando a janela e que estava vazia)
    print(f"   [deconflito] split={janelas_split} texto={janelas_texto} "
          f"letts={[(l['id'], l['start'], l['split']) for l in letts]}", flush=True)

    # ---------- captions ----------
    groups = build_timeline.group_captions(words, max_words=3)
    # sem legenda enquanto o hook esta na tela (so grupos que COMECAM depois do cap_gate) E
    # sem legenda do CORPO durante a janela do LOGO+CTA: o logo entra 3s ANTES do pill
    # (logo_s = cta_s - 3.0, ver calculo de logo_s mais abaixo) pra dar tempo de subir animado.
    # Gate so ate cta_start (o inicio do ultimo bloco) deixava a legenda do penultimo bloco
    # aparecer ATRAS do logo ja visivel (achado real no ad07: "automação ou" sobreposto ao
    # logo Operação Claude Code). Gate correto: cortar a partir de logo_s, nao de cta_start.
    # CTA/LOGO SEGUEM O ROTEIRO, nao o ultimo bloco (17/08/2026, reprovacao do diretor
    # de arte: "o unico CTA da peca fica 1,5s na tela"). O roteiro MARCA onde o logo
    # entra, com "+ logo" na instrucao do bloco. Ancorar no ultimo bloco fazia o CTA
    # nascer no bloco 15 do jh13, que tem 1,98s de fala, e o pico de intencao do anuncio
    # passava batido. Agora o CTA sobe no bloco que o roteiro marcou e FICA ate o fim.
    # O CTA sobe quando a imagem VOLTA pro Thales no fim (insert com dur_max), nao no
    # inicio do ultimo bloco. Tentativa anterior: ancorar no bloco que o roteiro marca
    # com "+ logo". Quebrou o anuncio, porque ESTA variavel e o corte da LEGENDA e o CTA
    # de verdade e o cta_s la embaixo. As duas ficaram desencontradas e o ad rodou 13s
    # com a tela literalmente vazia (gate: 17% sem texto). Agora ha UMA fonte: cta_start,
    # e cta_s copia dela. Se mexer aqui, o cta_s acompanha sozinho.
    # SO o ultimo insert do roteiro manda no CTA. Um cap no meio do anuncio (usado pra
    # cortar piscada dentro do asset) nao pode adiantar o CTA nem matar a legenda.
    _ult_insert = max((i for i, b in enumerate(blocks) if b["type"] == "insert"),
                      default=-1)
    _fim = [t for i, t in _retorno_avatar if i == _ult_insert]
    if _fim and _fim[-1] < spans[-1][0]:
        cta_start = _fim[-1]
        print(f"   [cta] imagem volta pro avatar em {cta_start:.2f}s: CTA fica "
              f"{spans[-1][1] - cta_start:.1f}s na tela", flush=True)
    else:
        cta_start = spans[-1][0]
    # A janela do logo TEM que ser curta. Historico: o gate era logo_start (cta-3s), que
    # matava 6.6s de fala legendada; troquei pra cta_start em 04/08 apoiado na premissa
    # "legenda em bottom:330 e logo em bottom:90 nunca se encostam". Depois eu mesmo subi
    # os dois pra safe zone do Reels (legenda 545, logo 390) e a premissa caiu: a legenda
    # passou a cair EM CIMA da linha "OPERAÇÃO" do logo (medido no ad01v2 aos 72.2s,
    # "máquina de conteúdo" com "conteúdo" ilegivel). Solucao que atende os dois lados:
    # encurtar a antecipacao do logo pra 0.9s (ainda anima) e cortar a legenda ai. Perde
    # 0.9s de legenda em vez de 3s, e nao sobrepoe.
    # ...e a antecipacao so vale quando o bloco ANTERIOR ao CTA e avatar. Se for insert,
    # o logo sobe por cima do b-roll: no ad02v2 ele caiu exatamente sobre a landing page
    # que exibe o PROPRIO logo Claude Code, dando dois wordmarks empilhados e a palavra
    # colada "Claude Codentre R$" (medido no frame 1352, t=45.07s). Sobre insert o logo
    # entra junto com o corte pro avatar.
    prev_e_insert = len(blocks) > 1 and blocks[-2]["type"] == "insert"
    LOGO_LEAD = 0.0 if prev_e_insert else 0.9
    logo_start = max(cta_start - LOGO_LEAD, 0.0)
    # filtrar so pelo START nao basta: um grupo que COMECA antes de logo_start mas
    # dura 1s continua na tela quando o logo sobe (visto no ad01v2: "o motor é mais."
    # atras do wordmark). Corta pelo start e TRUNCA o grupo que atravessa a fronteira,
    # assim a legenda vai ate o ultimo instante livre sem invadir o logo.
    groups = [g for g in groups if cap_gate < g["start"] < logo_start - 0.05]
    # Insert que JA CARREGA TEXTO PROPRIO (card de depoimento, print de prova social) nao
    # leva legenda por cima: e o mesmo principio que ja suprime legenda na janela do
    # lettering, pra nao empilhar dois textos. Defeito real que motivou isso: AD15 aos
    # 36,8s, a legenda "vendendo" caiu no meio do paragrafo do card do Marco Aurelio.
    if janelas_texto:
        n = 0
        for g in groups:
            if any(a <= g["start"] < b for a, b in janelas_texto):
                g["baixa"] = True
                n += 1
        if n:
            print(f"   [texto_proprio] {n} grupo(s) de legenda descido(s) pro rodape "
                  f"sobre card com texto", flush=True)

    # LOOK FECHADO: legenda baixa o ad INTEIRO (18/08/2026). No oficial_13 o
    # enquadramento e de rosto, quase sem peito: a posicao padrao da legenda
    # (calibrada pro plano medio) raspa o queixo e o gate de colisao reprova
    # (jh14 t=18s, 1,8% do rosto mesmo com punch discreto). No rodape ela cai
    # sobre a camiseta/mic e nunca disputa com o rosto.
    # MEDIDO, NAO PELO NOME DO LOOK (27/08/2026). A regra existia so pra `oficial_13`,
    # escrita a mao, e o `espuma_roxa` e igualmente fechado: em t=16,5s a legenda
    # "skill e essa" pousou na barba do Thales, 6,6% do rosto, com o queixo em y1390 e a
    # tinta em y1275-1340. Nome de look nao e criterio: o enquadramento e.
    # `medir_rosto` da a caixa no proprio avatar; queixo abaixo de 60% da altura do
    # quadro significa que nao sobra peito pra posicao padrao da legenda.
    _fechado = False
    try:
        import medir_rosto as _mr
        _cx = _mr.caixa_rosto(str(cfg.get("avatar", "")))
        if _cx:
            _fim = (_cx[0] + _cx[1]) / 1920.0
            _fechado = _fim > 0.60
            print(f"   [look] queixo do avatar em {_fim:.0%} da altura -> "
                  f"{'FECHADO, legenda no rodape' if _fechado else 'aberto, legenda padrao'}",
                  flush=True)
    except Exception as _e:
        print(f"   [look] nao consegui medir o avatar ({_e}); caindo no nome do look",
              flush=True)
    if _fechado or "oficial_13" in str(cfg.get("avatar", "")) or "_of13" in str(cfg.get("avatar", "")):
        n = 0
        for g in groups:
            if not g.get("costura") and not g.get("baixa"):
                g["baixa"] = True
                n += 1
        if n:
            print(f"   [look fechado] {n} grupo(s) de legenda no rodape "
                  f"(oficial_13 nao tem peito pra legenda padrao)", flush=True)
    # TELA DIVIDIDA: a legenda fica travada em bottom:545px, que num bloco cheio pousa
    # no peito do Thales. No split ele ocupa so a metade de baixo, entao os mesmos 545px
    # caem NOS OLHOS dele (medido no build de 18h58: faixa da legenda em y 1268-1356 e
    # os olhos em ~1200). A janela ja era coletada aqui e nunca usada pra isso.
    # CORTAR O GRUPO NA FRONTEIRA DE LAYOUT (27/08/2026, quarta passada).
    # Os tres criterios anteriores (comeco, meio, sobreposicao de 60%) erraram todos pelo
    # mesmo motivo: um grupo que ATRAVESSA a troca de layout nao tem posicao boa, porque
    # no split so a costura escapa do rosto e no avatar cheio e a costura que cai nele.
    # Escolher um lado sempre deixa o outro errado, e a medicao provou nas duas pontas:
    # por "meio" o "Code." pegou 3,90% do rosto, e por "60% dentro" a "pagina
    # completamente diferente" pegou 4,2% do outro lado.
    # Entao o grupo nao atravessa: ele vira DOIS, um de cada lado da fronteira, cada um
    # com a classe do seu layout. E o mesmo tratamento que o lettering ja recebe, e a
    # fala nao sofre porque a palavra continua no tempo dela.
    if janelas_split:
        _bordas = sorted({round(x, 3) for jan in janelas_split for x in jan})
        _novos, _cortados = [], 0
        for g in groups:
            _fatias = [g]
            for _b in _bordas:
                _saida = []
                for _f in _fatias:
                    if not (_f["start"] + 0.12 < _b < _f["end"] - 0.12):
                        _saida.append(_f); continue
                    _ini = [w for w in _f["words"] if (w["start"] + w["end"]) / 2 < _b]
                    _fim = [w for w in _f["words"] if (w["start"] + w["end"]) / 2 >= _b]
                    if not _ini or not _fim:
                        # NAO DA PRA CORTAR: o grupo tem uma palavra so, ou todas caem
                        # do mesmo lado. Entao APARA em vez de deixar atravessar.
                        # Medido no jh13: "publicacao" sozinho, 108,40 a 109,20, ficava
                        # 57,5% dentro do split. Abaixo dos 60% levava a posicao do
                        # avatar cheio (tinta y1415-1494) pra cima do rosto do painel de
                        # baixo (y1259-1887): 5,6% de cobertura em t=80,0s.
                        # As duas posicoes machucam nesse caso (a costura cai no rosto
                        # do avatar cheio, a baixa cai no rosto do split), entao a saida
                        # nao e escolher: e a palavra ficar menos tempo na tela, inteira
                        # de um lado so.
                        _antes = _b - _f["start"]
                        _depois = _f["end"] - _b
                        if _antes >= _depois and _antes >= 0.30:
                            _saida.append({**_f, "end": round(_b - 0.02, 3)})
                            _cortados += 1
                        elif _depois > _antes and _depois >= 0.30:
                            _saida.append({**_f, "start": round(_b + 0.02, 3)})
                            _cortados += 1
                        else:
                            _saida.append(_f)
                        continue
                    _saida.append({**_f, "words": _ini, "start": _f["start"],
                                   "end": round(_ini[-1]["end"], 3)})
                    _saida.append({**_f, "words": _fim, "start": round(_fim[0]["start"], 3),
                                   "end": _f["end"]})
                    _cortados += 1
                _fatias = _saida
            _novos.extend(_fatias)
        if _cortados:
            print(f"   [split] {_cortados} grupo(s) de legenda cortado(s) na fronteira de "
                  f"layout, pra nenhum atravessar o corte", flush=True)
        groups = _novos

    if janelas_split:
        # MARGEM de tolerancia (21/08/2026): grupo de legenda vem da transcricao
        # (timing da palavra), janela de split vem do ritmo.py (timing do corte). Os
        # dois nascem de fontes diferentes e um grupo pode comecar 0,3 a 0,5s ANTES da
        # janela abrir sem a legenda ter mudado de posicao ainda. Sem margem, esse
        # grupo cai na posicao PADRAO (calibrada pro avatar cheio) bem no instante em
        # que o corte pro split ja aconteceu na footage: foi o que causou a colisao
        # medida em t=39s do jh15 (legenda no rosto, mas o quadro ja era split havia
        # 0,35s). Alargar a janela por igual nos dois lados custa, no pior caso, um
        # grupo pousar na costura um pouco antes/depois do corte visual: cosmetico,
        # nunca colisao.
        # SO PRA FRENTE (27/08/2026). A folga era simetrica, e o comentario acima
        # justificava: "no pior caso um grupo pousa na costura um pouco antes ou depois
        # do corte visual: cosmetico, nunca colisao". Isso valia enquanto a costura
        # ficava LA EMBAIXO (tinta em y1796-1841), onde ela caia na camiseta em qualquer
        # layout. Hoje a costura subiu pra y967-1106, que so e seguro DURANTE o split:
        # num quadro de avatar cheio essa faixa cai no rosto.
        # Medido no jh13: o split termina em 80,64s e a legenda de 81,0s ainda herdava a
        # costura pela folga, com 1,6% do rosto coberto (teto 1,5%).
        # A folga de ENTRADA continua, e ela e a que resolve o problema original: grupo
        # que comeca um pouco antes da janela abrir, quando a footage ja cortou.
        n = 0
        for g in groups:
            # PELO MEIO DO GRUPO, NAO PELO COMECO (27/08/2026, segunda passada). Tirar
            # a folga de tras nao bastou: o grupo "publicacao e aprender" NASCE dentro
            # do split (108,40s, janela ate 108,86s) e vive 0,98s depois que ele acaba,
            # ou seja dois tercos da vida dele sao de avatar cheio, com a costura em
            # cima do rosto (1,6% de cobertura medida em t=81,0s).
            # O meio do grupo diz onde ele passa a maior parte do tempo. A minoria fica
            # na posicao errada por uma fracao de segundo, e nao ha posicao que sirva
            # pros dois layouts: no split so a costura escapa do rosto, no avatar cheio
            # e justamente a costura que cai nele.
            # POR SOBREPOSICAO, NAO POR INSTANTE (27/08/2026, terceira passada).
            # Decidir por um PONTO dentro de um intervalo produz colisao nas duas pontas,
            # e o diretor achou tres que sobreviveram: "Code." (75,67s) nasce e morre
            # inteiro em avatar cheio e virou costura so porque o meio dele caiu na folga
            # de 0,5s; "pagina completamente diferente" (27,47s) e "todo o processo"
            # (33,42s) tem o meio dentro do split e vivem 0,3s a 0,9s fora dele, com
            # 3,84% e 3,90% do rosto cobertos.
            # A pergunta certa nao e "onde esta o meio" e sim "quanto desse grupo vive
            # dentro do split": 60% ou mais, costura; menos, a posicao do avatar cheio.
            # A folga de entrada tambem foi a zero: ela existia pra cobrir 0,3 a 0,5s de
            # desencontro entre o timing da fala e o do corte, e a sobreposicao ja
            # absorve isso sozinha, sem esticar a janela pra fora do split.
            _dur_g = max(g["end"] - g["start"], 1e-6)
            _dentro = sum(max(0.0, min(g["end"], b) - max(g["start"], a))
                          for a, b in janelas_split)
            if _dentro / _dur_g >= 0.60:
                g["costura"] = True
                g.pop("baixa", None)
                n += 1
        if n:
            print(f"   [split] {n} grupo(s) de legenda na COSTURA dos paineis "
                  f"(nem na testa nem na boca dele)", flush=True)
    for g in groups:
        if g["end"] > logo_start:
            g["end"] = logo_start
    # suprime legenda que coincide com a janela de lettering (evita texto duplicado empilhado)
    # E TAMBEM o LEAD-IN: o grupo logo ANTES do lettering que ecoa a mesma frase. Ex real (ad08):
    # a legenda "o pulo do" (kw em serif) fechava ~0.05s ANTES do lettering "o pulo do gato"
    # comecar, entao a MESMA frase aparecia 2x (rodape depois peito). A janela crua nao pegava
    # esse grupo (ele nao sobrepoe, so encosta). Fix: remover grupos cujo texto INTEIRO faz parte
    # da frase (LEAD+KEY) do lettering dentro de uma janela retroativa. So mata o eco; legenda
    # nao relacionada perto do lettering fica.
    LETT_LOOKBACK = 2.6
    lett_phrases = []
    for l in letts:
        _txt = l["lead"] + " " + " ".join(
            [x["key"] for x in l.get("linhas", [])] or [l["key"]])
        toks = set(norm(t) for t in _txt.replace("<br>", " ").split() if norm(t))
        lett_phrases.append((l["start"], l["start"] + l["dur"], toks))

    def _echoes_lettering(g):
        gw = [norm(w["text"]) for w in g["words"] if norm(w["text"])]
        for ls, le, toks in lett_phrases:
            if g["start"] < le + 0.2 and g["end"] > ls - LETT_LOOKBACK and gw and all(x in toks for x in gw):
                return True
        return False

    groups = [g for g in groups
              if not any(g["start"] < we and g["end"] > ws for ws, we in lett_windows)
              and not _echoes_lettering(g)]
    caps_html = build_timeline._render_captions_html(groups)

    # ATENCAO: o HTML do lettering e montado AQUI, inline, e NAO pelo build_timeline. Eu
    # perdi um build inteiro editando a funcao equivalente la (que este ad nao usa) e
    # concluindo que o CSS estava errado. Mexeu no lettering? e nesta linha.
    letts_html = "\n".join(
        f'<div class="lett clip{" lett-split" if l.get("split") else ""}'
        f'{" lett-baixo" if l.get("baixo") else ""}'
        f'{" lett-pilha" if l.get("linhas") else ""}" id="{l["id"]}" '
        f'data-start="{l["start"]}" data-duration="{l["dur"]}" data-track-index="{32+i}">\n'
        f'  <div class="lead">{_sem_emoji(l["lead"])}</div>\n'
        + (
            # _sem_emoji AQUI TAMBEM (19/08/2026): este ramo, o da PILHA, era o unico
            # que mandava o texto cru pro HTML. Como o unico bloco `pilha` do jh13 e a
            # lista de dor com tres ❌, o anuncio saiu com emoji na tela por 3,87s
            # DEPOIS de eu declarar "sem emoji" no changelog. O marcador de negacao
            # agora e a barra vermelha do CSS (.lett-pilha .key::before), nao pictograma.
            "".join(f'  <div class="key" data-delay="{x["delay"]}">'
                    f'{_sem_emoji(x["key"])}</div>\n'
                    for x in l["linhas"])
            if l.get("linhas") else
            f'  <div class="key{" key-longa" if len(l["key"]) > 18 else ""}">'
            f'{_sem_emoji(l["key"])}</div>\n')
        + ('  <img class="lett-logo" src="logo.png" alt="">\n' if l.get("logo") else "")
        + '</div>'
        for i, l in enumerate(letts))

    # ---------- CTA/logo: ultimo bloco (lettering_logo ou ultimo bloco do roteiro) ----------
    # MESMA fonte do corte da legenda (cta_start). Divergir as duas deixa a tela vazia
    # entre o fim da legenda e a subida do CTA. Nao separar de novo.
    cta_s = round(cta_start, 2)
    # mesmo LOGO_LEAD usado no gate da legenda acima: os dois TEM que casar, senao
    # volta a sobreposicao legenda/logo.
    logo_s = round(max(cta_s - LOGO_LEAD, 0.0), 2)

    # ---------- GATE DE TELA VAZIA (barato, antes do render) ----------
    # O gate final mede isto no MOV alpha e reprovou o build de 17/08 com 13s de tela
    # vazia. So que ele custa 13 minutos de render pra falar. Aqui a mesma conta sai de
    # graca: uniao das janelas de texto (hook, legenda, lettering, CTA) e o maior buraco.
    # Causa raiz que motivou o gate: cortar a legenda num tempo e subir o CTA em outro.
    _janelas = [(0.0, float(hook_dur))]
    _janelas += [(float(g["start"]), float(g["end"])) for g in groups]
    _janelas += [(float(a), float(b)) for a, b in lett_windows]
    _janelas += [(float(cta_s), float(total))]
    _janelas.sort()
    _fim, _pior, _quando = 0.0, 0.0, 0.0
    for a, b in _janelas:
        if a - _fim > _pior:
            _pior, _quando = a - _fim, _fim
        _fim = max(_fim, b)
    if total - _fim > _pior:
        _pior, _quando = total - _fim, _fim
    # A janela do CTA suprime a legenda do corpo. Se ela for longa, o anuncio roda mudo
    # em feed silencioso sem o gate de tela vazia perceber (pro alpha, CTA e texto).
    MAX_JANELA_CTA = 12.0   # segundos de audio, ~8,9s de tela
    _janela_cta = total - cta_s
    print(f"   [cta] janela do CTA: {_janela_cta:.2f}s de audio "
          f"({_janela_cta / 1.35:.2f}s de tela, teto {MAX_JANELA_CTA}s)", flush=True)
    if _janela_cta > MAX_JANELA_CTA:
        sys.exit(
            f"JANELA DE CTA LONGA DEMAIS: {_janela_cta:.2f}s de audio a partir de "
            f"{cta_s:.2f}s, num total de {total:.2f}s. A legenda do corpo e cortada "
            f"nessa janela, entao {100 * _janela_cta / total:.0f}% do anuncio rodaria sem "
            f"legenda. Quase sempre a causa e um insert com dur_max no MEIO do roteiro "
            f"puxando o cta_start pra tras.")

    MAX_VAO = 3.3   # segundos de audio; o ad e acelerado depois, ~2,4s de tela
    print(f"   [tela] maior vao sem texto: {_pior:.2f}s de audio em {_quando:.2f}s "
          f"(teto {MAX_VAO}s)", flush=True)
    if _pior > MAX_VAO:
        sys.exit(
            f"TELA VAZIA: {_pior:.2f}s sem nenhum texto a partir de {_quando:.2f}s "
            f"(audio). Quase sempre e o corte da legenda (cta_start) desencontrado da "
            f"subida do CTA (cta_s), ou um lettering que nao encaixou. Conferir os dois "
            f"antes de renderizar.")

    # ---------- montar html ----------
    html = tmpl.read_text()
    html = html.replace("<!-- INJECT:captions -->", caps_html)
    html = html.replace("<!-- INJECT:letterings -->", letts_html)

    # ---------- CHIPS flutuantes (18/08/2026, item 3 do brief) ----------
    # Vao longo de avatar sem insert e sem lettering = trecho parado. O chip poe um
    # elemento chamativo com a PALAVRA-CHAVE da fala daquele instante (REF-03 faz isso
    # com chips de UI). Data-driven do plano de ritmo: nada e chutado.
    _runs = []
    _ra = None
    for _x in _plano_ritmo:
        if _x["tipo"] != "insert":
            _ra = _x["s"] if _ra is None else _ra
            _rb = _x["e"]
        else:
            if _ra is not None:
                _runs.append((_ra, _rb))
            _ra = None
    if _ra is not None:
        _runs.append((_ra, _rb))
    chips = []
    _ult_chip = -1e9
    # VAOS MAIORES PRIMEIRO (18/08/2026): com a ordem cronologica, o espacamento
    # bloqueava o chip justamente do maior vao (jh16: 14s de fala sem chip porque um
    # vao menor 7,7s antes ja tinha levado o dele). Espacamento minimo 6s.
    _runs = sorted(_runs, key=lambda r: r[1] - r[0], reverse=True)
    for _ra, _rb in _runs:
        if _rb - _ra < 7.0 or len(chips) >= 4:
            continue                       # vao curto ja e dinamico por natureza
        _tc = _ra + 1.2
        if _tc < 5.5:
            _tc = 5.5                      # nunca dentro do hook
        for _ls, _le in lett_windows:      # lettering ja e evento: chip espera a vez
            if _ls - 0.4 < _tc < _le + 0.4:
                _tc = _le + 0.5
        if _tc > min(_rb - 2.8, logo_s - 3.2) or any(abs(_tc - c['t']) < 6.0 for c in chips):
            continue
        # palavra do chip: kw da fala no instante; senao a palavra mais longa
        _cands = [w for g in groups for w in g["words"]
                  if _tc - 0.5 <= float(w["start"]) <= _tc + 3.5]
        _kw = next((w["text"] for w in _cands if w.get("kw")), None)
        if not _kw:
            # palavra mais longa que NAO seja muleta: adverbios em -mente e
            # conectivos compridos ganhavam sempre e o chip saia com
            # "PRATICAMENTE", que nao carrega conteudo nenhum (visto no jh14)
            _mulet = {"porque", "enquanto", "tambem", "também", "depois",
                      "agora", "ainda", "entao", "então", "quando", "nenhum",
                      "nenhuma", "qualquer", "alguma", "mesmo", "mesma",
                      "muita", "muito", "aquele", "aquela", "aquilo"}
            _limpa = [w["text"].strip(".,?!;:") for w in _cands]
            _limpa = [t for t in _limpa if len(t) >= 5
                      and not t.lower().endswith("mente")
                      and t.lower() not in _mulet]
            _kw = max(_limpa, key=len) if _limpa else None
        if not _kw:
            continue
        chips.append({"t": round(_tc, 2), "kw": _kw.strip(".,?!;:").upper()})
    if chips:
        print(f"   [chips] {len(chips)}: " +
              ", ".join(f"{c['kw']}@{c['t']}s" for c in chips), flush=True)
    chips_html = "\n".join(
        f'<div class="chip clip" id="chip{k}" data-start="{c["t"]}" '
        f'data-duration="2.6" data-track-index="{58 + k}">'
        f'<span class="dot"></span>{c["kw"]}</div>'
        for k, c in enumerate(chips))
    html = html.replace("<!-- INJECT:chips -->", chips_html)
    html = re.sub(r"<!--\s*INJECT:preset:[a-z-]+\s*-->", "", html)

    # durações
    html = html.replace('data-start="0" data-duration="55.36"', f'data-start="0" data-duration="{total}"')

    # hook: fica ~4s na tela (hook_dur/hook_fade/hook_gone ja calculados apos os spans)
    hk = {k: _sem_emoji(v) for k, v in cfg["hook"].items()}
    html = html.replace('id="hook" class="clip" data-start="0" data-duration="2.5"',
                        f'id="hook" class="clip" data-start="0" data-duration="{hook_dur}"')
    html = html.replace('<div data-hf-id="hf-bc1a" class="eyebrow">uma skill de</div>', f'<div data-hf-id="hf-bc1a" class="eyebrow">{hk["eyebrow"]}</div>')
    html = html.replace('<div data-hf-id="hf-8q5w" class="l1">criação de</div>', f'<div data-hf-id="hf-8q5w" class="l1">{hk["l1"]}</div>')
    html = html.replace('<div data-hf-id="hf-ons9" class="accent">páginas</div>', f'<div data-hf-id="hf-ons9" class="accent">{hk["accent"]}</div>')
    html = html.replace('#hook .l1 { font-family:"Inter"; font-weight:300; color:#fff;',
                        '#hook .l1 { font-family:"Inter"; font-weight:300; color:#fff; text-align:center;')
    html = html.replace('#hook .accent { font-family:"Playfair Display", serif; font-weight:600; font-style:italic;',
                        '#hook .accent { font-family:"Playfair Display", serif; font-weight:600; font-style:italic; text-align:center;')

    # HOOK "PUNCH" (17/08/2026, pedido da Jheni: "esse primeiro lettering deveria ser
    # mais chamativo", com a ref instagram.com/p/DZ-GKJ7u54z). Referencia: sans
    # condensado PESADO, tudo em caixa alta, entrelinha apertada, palavra de enfase bem
    # maior e sombra dura. O serif elegante do padrao e o oposto disso.
    # E VARIANTE, nao troca global: os ads 01 a 21 continuam no estilo editorial.
    if (cfg.get("hook") or {}).get("style") == "punch":
        html = html.replace("</style>", """
      /* variante PUNCH do hook: ver comentario em gen_ad_v2.py */
      /* justify-content:flex-start + margin-top pequeno sobe o bloco pro TERCO
         SUPERIOR. No bloco de tela dividida o padrao (centro, margin-top 150px)
         caia exatamente em cima do rosto do Thales no painel de baixo. */
      /* SCRIM PROPRIO: com o painel de cima PREENCHIDO, o texto da propria pagina
         colidia com o hook e os dois ficavam ilegiveis. Faixa escura atras do bloco
         resolve sem escurecer o quadro inteiro. */
      /* 70px punha a linha 3 do gancho ("COM CARA DE I.A") ate x993, dentro da
         coluna de curtir/comentar do Reels, nos 2,4s que decidem o scroll. Eu tinha
         subido o padding no TEMPLATE e nao vi que este `!important` inline vence:
         dois lugares definem o hook, e eu emendei o que a busca achou primeiro. */
      #hook.punch { padding:0 140px; justify-content:flex-start !important;
        /* scrim mais leve: medido, o AD13 abria 57% e o AD16 59% mais escuros
           que o resto do anuncio, e o quadro 0 e o poster no feed. O texto e caixa alta
           pesada com sombra tripla, entao aguenta bem menos fundo. */
        background:linear-gradient(180deg, rgba(4,5,10,0) 0%, rgba(4,5,10,.26) 12%,
          rgba(4,5,10,.56) 24%, rgba(4,5,10,.56) 44%,
          rgba(4,5,10,0) 66%) !important; }
      #hook.punch .hook-inner { align-items:center !important; gap:0 !important;
        margin-top:310px !important; }
      #hook.punch .eyebrow { font-family:"Inter"; font-weight:800; font-size:46px;
        letter-spacing:1px; margin-bottom:6px; color:#fff;
        text-shadow:0 4px 0 rgba(0,0,0,.55), 0 8px 30px rgba(0,0,0,.95); }
      #hook.punch .l1 { font-family:"Inter"; font-weight:800; font-size:64px;
        text-transform:uppercase; line-height:.98; letter-spacing:-1px; color:#fff;
        text-shadow:0 4px 0 rgba(0,0,0,.55), 0 8px 30px rgba(0,0,0,.95); }
      #hook.punch .accent { font-family:"Inter", sans-serif !important;
        font-style:normal !important; font-weight:900 !important; font-size:104px !important;
        text-transform:uppercase; line-height:.96 !important; letter-spacing:-2.5px !important;
        color:#fff; text-shadow:0 5px 0 rgba(0,0,0,.6), 0 10px 36px rgba(0,0,0,.95) !important; }
    </style>""")
        html = html.replace('id="hook" class="clip"', 'id="hook" class="clip punch"')
    html = html.replace(
        'tl.to("#hook .hook-inner", { scale: 1.04, duration: 1.7, ease: "sine.inOut" }, 0.8);',
        'tl.to("#hook .hook-inner", { scale: 1.04, duration: 1.7, ease: "sine.inOut" }, 0.8);\n'
        f'      tl.to("#hook", {{ opacity: 0, duration: 0.4, ease: "power1.in" }}, {hook_fade});')

    # grade quente 50% (calibrada pro cenario colorido; validada no piloto)
    html = html.replace(
        "radial-gradient(130% 100% at 50% 22%, rgba(255,193,128,0.16), rgba(255,150,80,0.05) 45%, transparent 72%)",
        "radial-gradient(130% 100% at 50% 22%, rgba(255,193,128,0.08), rgba(255,150,80,0.025) 45%, transparent 72%)")
    html = html.replace(
        "linear-gradient(180deg, rgba(255,168,92,0.06) 0%, transparent 38%, rgba(28,14,4,0.16) 100%)",
        "linear-gradient(180deg, rgba(255,168,92,0.03) 0%, transparent 38%, rgba(28,14,4,0.16) 100%)")

    # brolls html + js
    bh, bj = [], []
    for k, b in enumerate(brolls):
        # banda dos brolls: 8..29 (vid par, scrim impar) fica ABAIXO do #caps(30)
        # e das letterings(32/33) mesmo com 11 brolls (k ate 10). Antes era 10+2k,
        # que colidia com o #caps na track 30 quando havia 11 brolls.
        tk, sk, tag_tk = 8 + 2 * k, 9 + 2 * k, 50 + k
        bid = f"b{k+1}"
        bh.append(
            f'<div id="{bid}_scrim" class="broll-scrim clip" data-start="{b["s"]}" data-duration="{b["d"]}" data-track-index="{sk}"></div>\n'
            f'<div id="{bid}_tag" class="broll-tag clip" data-start="{b["s"]}" data-duration="{b["d"]}" data-track-index="{tag_tk}">'
            f'<span class="rec"></span><span class="t">{b["label"]}</span></div>\n'
            f'<video id="{bid}_vid" class="broll-vid clip" src="{b["src"]}" muted playsinline data-start="{b["s"]}" data-duration="{b["d"]}" data-track-index="{tk}"></video>')
        bj.append({"id": bid, "src": b["src"], "start": b["s"], "dur": b["d"], "tk": tk, "sk": sk})
    html = re.sub(r"<!-- B-ROLLS \(injected\) -->.*?<!-- LOWER THIRD -->",
                  "<!-- B-ROLLS (injected) -->\n" + "\n".join(bh) + "\n\n      <!-- LOWER THIRD -->", html, flags=re.S)
    html = re.sub(r"const BROLLS = \[.*?\];", "const BROLLS = " + json.dumps(bj, ensure_ascii=False) + ";", html, flags=re.S)

    # (lower-third/selo removido a pedido: sem bolinha do apresentador em nenhum ad)

    # CTA + logo
    # cta_sem_lead: tira o "toca em" do bloco de CTA quando o enquadramento nao tem
    # faixa livre pros tres elementos (lead + pill + logo). POR AD: a conta depende de
    # onde o queixo dele cai naquele look. Ver D8 da direcao do jh13 (18/08/2026).
    if cfg.get("cta_sem_lead"):
        html = html.replace(
            '<div data-hf-id="hf-laqu" class="lead">toca em</div>\n        ', "", 1)
        print("   [cta] sem o lead 'toca em': so pill + logo", flush=True)
    html = html.replace('<div data-hf-id="hf-wto1" class="pill" id="cta-pill">saiba mais</div>',
                        f'<div data-hf-id="hf-wto1" class="pill" id="cta-pill">{cfg.get("cta_label","saiba mais")}</div>')
    # O CTA precisa saber se nasce em cima de tela dividida (ver .cta-split no template).
    # `janelas_split` ja existe aqui pra legenda e pro lettering; o CTA era o unico dos
    # tres que nao consultava, e por isso pousava no rosto quando o ultimo bloco era split.
    _cta_no_split = any(a <= cta_s < b for a, b in janelas_split)
    if _cta_no_split:
        print(f"   [cta] {cta_s:.2f}s cai em tela dividida: descendo o CTA e o logo "
              f"(senao pousam no rosto)", flush=True)
        html = html.replace('id="cta" class="clip"', 'id="cta" class="clip cta-split"', 1)
        html = html.replace('id="ev-logo" class="clip"', 'id="ev-logo" class="clip logo-split"', 1)
    html = html.replace('data-start="50.4" data-duration="4.96" data-track-index="46"',
                        f'data-start="{cta_s}" data-duration="{round(total-cta_s,2)}" data-track-index="46"')
    html = html.replace('data-start="46.7" data-duration="8.68" data-track-index="48"',
                        f'data-start="{logo_s}" data-duration="{round(total-logo_s,2)}" data-track-index="48"')
    html = html.replace('}, 50.5);', f'}}, {cta_s + 0.1:.2f});')
    html = html.replace('}, 50.6);', f'}}, {cta_s + 0.2:.2f});')
    html = html.replace('}, 50.75);', f'}}, {cta_s + 0.35:.2f});')
    html = html.replace('}, 51.0);', f'}}, {cta_s + 0.6:.2f});')
    html = html.replace('}, 46.9);', f'}}, {logo_s + 0.2:.2f});')

    # beat P&B fora
    html = re.sub(r'// ===== BEAT PRETO E BRANCO.*?tl\.to\("#a-roll", \{ "--bw": 0[^;]*;\n',
                  "// (beat P&B do reelC nao usado)\n", html, flags=re.S)

    # grid-wipes nas fronteiras de grupos de brolls
    grupos = []
    for b in brolls:
        e = b["s"] + b["d"]
        if grupos and b["s"] - grupos[-1][1] <= XFADE_GAP:
            grupos[-1] = (grupos[-1][0], e)
        else:
            grupos.append((b["s"], e))
    # SO wipe de ENTRADA (avatar -> insert): elimina o flash preto do wipe de saida (D2)
    # e a transicao dupla (D4). O retorno insert -> avatar e um fade limpo do proprio broll.
    # Guard gs>0.34 pula o wipe da abertura (que ja e o fade do hook) e evita tempo negativo.
    wipes = []
    for gs, ge in grupos:
        if gs > 0.34 and abs(gs - HOOK_END) > 0.3:
            wipes.append(round(gs - 0.34, 2))
    # WIPE DE GRADE DESLIGADO (19/08/2026). Medido no arquivo entregue, varrendo a cor
    # da celula quadro a quadro: pico de cobertura de 98,5% a 99,5%, com 5 a 7 quadros
    # acima de 70% em CADA um dos 4 surtos. Sao 0,17 a 0,23s de tela praticamente
    # apagada, e um dos surtos cai aos 7,2s, dentro do hook, partindo o lettering no
    # meio. O Julio descreveu como "zero evolucao" e a peca le como arquivo corrompido.
    #
    # Duas tentativas minhas de salvar o efeito falharam e estao registradas pra nao
    # repetir: (1) antecipar a saida de t+0,9 pra t+0,45 nao resolve, porque a entrada
    # so fecha em t+0,68 (duration 0,4 + stagger 0,28) e sobra janela de cobertura
    # total; (2) tingir a celula de #191129 em vez de #05060a so trocou a cor do
    # apagao. O comentario antigo afirmava "a grade NUNCA cobre 100%": era falso.
    #
    # A maquinaria fica no lugar (template e agrupamento) porque a decupagem das
    # referencias ainda vai dizer se algum wipe tem lugar na gramatica. Se voltar,
    # precisa das TRES mudancas juntas: z-index abaixo de lettering e legenda (hoje 60,
    # acima de tudo), saida em t+0,25 e stagger.amount 0,45, com o gate de cobertura
    # chapada barrando qualquer quadro acima de 70%.
    wipe_js = ""
    html = html.replace("/* INJECT:wipes */", wipe_js)

    # ---------- prancha.json: a linha do tempo em dados, pro diretor de arte ----------
    # Tudo em tempo de AUDIO (1x). O arquivo entregue roda ACCEL mais rapido, entao a
    # prancha converte na hora de rotular. Ver prancha_direcao.py.
    _prancha = {
        "ad": ad, "look": look, "total": round(float(total), 2),
        "accel": 1.35,
        "hook": {"fim": round(float(hook_dur), 2),
                 "texto": {k: v for k, v in (cfg.get("hook") or {}).items()}},
        "cta": {"inicio": cta_s, "logo": logo_s, "label": cfg.get("cta_label", "")},
        "blocos": [{"i": i, "tipo": blocks[i]["type"], "instr": blocks[i]["instr"],
                    "s": round(float(a), 2), "e": round(float(b), 2),
                    "dur": round(float(b - a), 2)}
                   for i, (a, b) in enumerate(spans)],
        "inserts": [{"src": b["src"], "s": round(float(b["s"]), 2),
                     "d": round(float(b["d"]), 2), "label": b.get("label", "")}
                    for b in brolls],
        "letterings": [{"id": l["id"], "lead": l["lead"], "key": l["key"],
                        "s": float(l["start"]), "d": float(l["dur"]),
                        "split": bool(l.get("split")), "baixo": bool(l.get("baixo")),
                        "pilha": bool(l.get("linhas")),
                        "linhas": l.get("linhas") or []}
                       for l in letts],
        "legendas": [{"s": round(float(g["start"]), 2), "e": round(float(g["end"]), 2)}
                     for g in groups],
        "vao_sem_texto": {"maior": round(float(_pior), 2), "em": round(float(_quando), 2)},
    }
    (out / "prancha.json").write_text(json.dumps(_prancha, ensure_ascii=False, indent=2))
    # UMA VERDADE SO SOBRE ONDE HA SPLIT (27/08/2026). O `ritmo.py` marca `layout` em
    # TODA fatia de insert, mas a footage so honra isso quando o config do insert tem
    # `split: true`; sem a flag ela renderiza tela cheia. Quem sabe disso e este motor,
    # que le as duas coisas, e nao o plano de ritmo cru.
    # O gate de colisao estava lendo o plano cru e por isso acusava 14 colisoes em
    # trechos que a tela mostra como insert em tela cheia, sem apresentador nenhum:
    # falso positivo com a mesma cara de defeito real. Aqui saem as janelas de verdade.
    (out / "janelas_split.json").write_text(json.dumps(
        {"segs": [{"s": a_, "e": b_, "layout": "split"} for a_, b_ in janelas_split]},
        ensure_ascii=False, indent=2))
    (out / "index.html").write_text(html)
    print(f"[{ad} {look}] total={total}s | {len(brolls)} brolls | {len(letts)} letterings | "
          f"{len(groups)} grupos de legenda | CTA {cta_s}s | wipes {wipes}")
    for b in brolls:
        print(f"   {b['src']:14} {b['s']:6.2f}s +{b['d']:5.2f}s  {b['label']}")


if __name__ == "__main__":
    main(sys.argv[1])
