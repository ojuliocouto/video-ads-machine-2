#!/usr/bin/env python3
"""Ritmo de corte: um so ponto de verdade pros dois lados da comparacao.

Existe porque "dinamico" virou opiniao. O Julio mandou tres referencias e a Jheni disse
que o anuncio "poderia ser mais acelerado"; sem numero, cada rodada era palpite. Medido
com o MESMO metodo nos dois lados (deteccao de cena, limiar 0,30), em 18/08/2026:

    ref1 Da0WA9pAtDv  27,8 cortes/min   plano medio 2,16s
    ref2 DbBFmwMgJwn  18,9              3,17s
    ref3 DXRwBdlgNLj  19,5              3,08s
    AD13 nosso         4,7             12,80s
    AD14 nosso         7,6              7,91s

O alvo sai dessas referencias, nao de mim. As fixtures vivem em refs/vaibhav/ e o teste
`test_medir_ritmo.py` confere que o medidor concorda com elas: se ele nao pontuar as tres
entre 18 e 28 cortes/min, o errado e o medidor.

ARMADILHA CONHECIDA: `refs/sobral/ref_hook.mp4` tambem e referencia da leva, mandada pela
Jheni, mas de HOOK. Ela tem 2,1 cortes/min. Medir dinamica por ela leva a conclusao
oposta. O teste cobre isso.

Uso:
    python3 medir_ritmo.py <arquivo.mp4> [...]     mede e imprime
    python3 medir_ritmo.py --prancha <prancha.json>  estima ANTES do render
"""
import json
import re
import subprocess
import sys
from pathlib import Path

LIMIAR_CENA = 0.30      # o mesmo dos dois lados; mudar aqui invalida a comparacao
MIN_CORTES_MIN = 16.0   # piso; a referencia mais lenta das tres da 18,9
PLANO_LONGO_S = 6.0     # a partir daqui o plano conta como "parado"
# TETO DE TEMPO PARADO, nao de plano isolado. A primeira versao reprovava por "maior
# plano acima de 6s" e reprovava a PROPRIA referencia: a ref1 segura um plano de 13,2s
# e mesmo assim faz 27,8 cortes/min. Gate mais duro que a referencia reprovaria uma peca
# indistinguivel dela. Medido: refs 16,0% / 22,4% / 31,2% do tempo em plano longo; nossos
# ads 86,8% a 93,5%. O teto de 40% separa sem encostar em nenhum dos dois lados.
MAX_CORTES_MIN = 32.0   # teto: a referencia mais rapida faz 27,8. Gate que aprova 40
                        # aprovaria uma peca mais picotada que a propria referencia, e o
                        # Julio ja reclamou de insert "saindo da tela MUITO rapido".
MAX_FRAC_LENTA = 0.40
MAX_PLANO_S = 14.0      # teto absoluto generoso: a pior referencia segura 13,2s
ALVO_PLANO_S = (2.4, 3.4)


def _dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def cortes_de(p):
    """Instantes de corte detectados no arquivo, em segundos.

    CEGO EM MATERIAL ESCURO. `scene` compara diferenca de pixel, entao um corte entre
    dois quadros escuros nao registra. Serve pra material de terceiro (referencia), onde
    nao existe plano; para os NOSSOS ads use medir_do_plano(), que e exato.
    """
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(p), "-filter_complex",
         f"select='gt(scene,{LIMIAR_CENA})',metadata=print:file=-", "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", r.stdout + r.stderr)]


def _planos(cortes, dur):
    """Duracao de cada plano, a partir dos instantes de corte."""
    marcos = [0.0] + sorted(cortes) + [dur]
    return [b - a for a, b in zip(marcos, marcos[1:]) if b > a]


def medir(p):
    p = Path(p)
    dur = _dur(p)
    cortes = cortes_de(p)
    planos = _planos(cortes, dur)
    lentos = [x for x in planos if x > PLANO_LONGO_S]
    return {
        "arquivo": p.name,
        "frac_lenta": round(sum(lentos) / dur, 4) if dur else 0.0,
        "planos_longos": len(lentos),
        "dur": round(dur, 2),
        "cortes": len(cortes),
        "cortes_min": round(len(cortes) / (dur / 60), 2) if dur else 0.0,
        "plano_medio": round(dur / max(len(cortes), 1), 2),
        "maior_plano": round(max(planos), 2) if planos else round(dur, 2),
        "planos": [round(x, 2) for x in planos],
        "instantes": [round(x, 2) for x in sorted(cortes)],
    }


def medir_prancha(caminho):
    """Estimativa ANTES do render, a partir dos blocos do prancha.json.

    E um PISO, nao a medida final: conta so corte editorial (troca de bloco) e ignora
    tanto o corte interno de asset pre-concatenado quanto o fato de que crossfade longo
    nao le como corte. Serve pra reprovar cedo, de graca, antes de 20 minutos de render.
    """
    pr = json.loads(Path(caminho).read_text())
    accel = pr.get("accel", 1.35)
    dur = pr["total"] / accel
    planos = [b["dur"] / accel for b in pr["blocos"]]
    n = max(len(planos) - 1, 0)
    lentos = [x for x in planos if x > PLANO_LONGO_S]
    return {
        "arquivo": f"{pr['ad']} (prancha)",
        "frac_lenta": round(sum(lentos) / dur, 4) if dur else 0.0,
        "planos_longos": len(lentos),
        "dur": round(dur, 2),
        "cortes": n,
        "cortes_min": round(n / (dur / 60), 2) if dur else 0.0,
        "plano_medio": round(dur / max(n, 1), 2),
        "maior_plano": round(max(planos), 2) if planos else 0.0,
        "planos": [round(x, 2) for x in planos],
        "instantes": [],
    }


def medir_do_plano(ad, prancha=None):
    """Ritmo EXATO dos nossos ads, a partir do plano que o ritmo.py produziu.

    Nos controlamos onde cortamos, entao nao ha por que adivinhar por deteccao. Esta e a
    medida que vale no gate; `medir()` fica como referencia cruzada.
    """
    import sys as _sys
    # (migracao 26/08/2026) codigo agora vizinho; import direto resolve
    import ritmo as _R
    from caminhos import V2L as L
    if prancha is None:
        cand = sorted(L.glob(f"render-{ad}-*-ovl/prancha.json"),
                      key=lambda x: x.stat().st_mtime)
        if not cand:
            cand = sorted((L / "prancha" / ad).glob("prancha.json"))
        if not cand:
            raise FileNotFoundError(f"sem prancha.json para {ad}")
        prancha = cand[-1]
    pr = json.loads(Path(prancha).read_text())
    ac = pr.get("accel", 1.35)
    from caminhos import INPUTS as _IN
    ins = json.loads((_IN /
                      f"{ad}_inserts.json").read_text())
    vals = list(ins.values())
    n, blocos = 0, []
    for b in pr["blocos"]:
        cfg = {}
        if b["tipo"] == "insert":
            cfg = vals[n] if n < len(vals) else {}
            n += 1
        blocos.append({"tipo": "insert" if b["tipo"] == "insert" else "orig",
                       "s": b["s"], "e": b["e"],
                       "crop": cfg.get("crop"), "dur_max": cfg.get("dur_max")})
    segs = _R.plano_de_ritmo(blocos)
    dur = pr["total"] / ac
    planos = [(x["e"] - x["s"]) / ac for x in segs]
    lentos = [x for x in planos if x > PLANO_LONGO_S]
    return {
        "arquivo": f"{ad} (plano)",
        "dur": round(dur, 2),
        "cortes": len(segs) - 1,
        "cortes_min": round((len(segs) - 1) / (dur / 60), 2) if dur else 0.0,
        "plano_medio": round(dur / max(len(segs), 1), 2),
        "maior_plano": round(max(planos), 2) if planos else 0.0,
        "frac_lenta": round(sum(lentos) / dur, 4) if dur else 0.0,
        "planos_longos": len(lentos),
        "planos": [round(x, 2) for x in planos],
        "instantes": [],
    }


def aprova(m):
    """Devolve (ok, motivos). Reprovar sem dizer o motivo nao serve pra nada."""
    motivos = []
    if m["cortes_min"] < MIN_CORTES_MIN:
        motivos.append(f"ritmo lento: {m['cortes_min']:.1f} cortes/min "
                       f"(minimo {MIN_CORTES_MIN:.0f}; a referencia faz 19 a 28)")
    if m["cortes_min"] > MAX_CORTES_MIN:
        motivos.append(f"picotado demais: {m['cortes_min']:.1f} cortes/min "
                       f"(teto {MAX_CORTES_MIN:.0f}; a referencia mais rapida faz 27,8)")
    if m.get("frac_lenta", 0) > MAX_FRAC_LENTA:
        piores = sorted(m["planos"], reverse=True)[:3]
        motivos.append(f"tempo parado: {m['frac_lenta']:.0%} do anuncio em plano acima de "
                       f"{PLANO_LONGO_S:.0f}s (teto {MAX_FRAC_LENTA:.0%}; a referencia "
                       f"fica entre 16% e 31%). Os tres maiores planos: "
                       f"{', '.join(f'{x:.1f}s' for x in piores)}")
    if m["maior_plano"] > MAX_PLANO_S:
        motivos.append(f"plano unico de {m['maior_plano']:.1f}s "
                       f"(teto {MAX_PLANO_S:.0f}s; a pior referencia segura 13,2s)")
    return (not motivos), motivos


def imprimir(m, alvo=True):
    ok, motivos = aprova(m)
    print(f"  {m['arquivo'][:44]:46s} {m['dur']:6.1f}s  {m['cortes']:3d} cortes  "
          f"{m['cortes_min']:5.1f}/min  plano medio {m['plano_medio']:5.2f}s  "
          f"maior {m['maior_plano']:5.2f}s  parado {m.get('frac_lenta', 0):5.1%}")
    if alvo:
        for x in motivos:
            print(f"      REPROVA: {x}")
        if ok:
            print("      PASSA")
    return ok


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    if args[0] == "--plano":
        todos = [medir_do_plano(a) for a in args[1:]]
        ok = all([imprimir(m) for m in todos])
        return 0 if ok else 1
    if args[0] == "--prancha":
        todos = [medir_prancha(a) for a in args[1:]]
    else:
        todos = [medir(a) for a in args]
    # NAO usar all(...) com gerador: ele faz short-circuit e para de imprimir no
    # primeiro que reprova, escondendo os demais. Medir varios e mostrar um so e pior
    # que nao medir, porque parece cobertura.
    ok = all([imprimir(m) for m in todos])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
