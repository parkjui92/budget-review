"""예결산 검토보고서 문체·골격 계량 — 같은 방법으로 여러 묶음을 잰다.

    python3 measure.py txt/결산_예결위 txt/예산안_예결위 txt/예산안_상임위 > 결과.json

입력은 hwp5txt 덤프(.txt). 표는 <표> 자리표시자로 평탄화되어 있으므로
표 안의 글자는 세지 않는다(본문 문장만 잰다). 표준 라이브러리만 쓴다.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

HANGUL = re.compile(r"[가-힣]")
TOC_TAIL = re.compile(r"\s*\d{1,4}\s*$")          # 차례 줄: 끝에 쪽번호
ITEM = re.compile(r"^\s*(\d{1,3})\.\s*(.{2,120}?)\s*[:：]\s*(.{2,})$")
ROMAN = re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.\s*(.+)$")
BRACKET = re.compile(r"^\s*【\s*(.+?)\s*】\s*$")
ANGLE = re.compile(r"^\s*<\s*([가-힣 ]{1,10})\s*>\s*\d*\s*$")
GANA = re.compile(r"^\s*[가나다라마바사]\.\s*(.+)$")
NUM = re.compile(r"^\s*\d{1,2}\.\s*(.+)$")
CAPTION = re.compile(r"^\s*\[(.{2,120})\]\s*$")


def jong(ch):
    """한글 음절의 종성 인덱스(0=받침 없음)."""
    if not ("가" <= ch <= "힣"):
        return -1
    return (ord(ch) - 0xAC00) % 28


def last_hangul(s):
    for ch in reversed(s):
        if HANGUL.match(ch):
            return ch
    return ""


def body_lines(text):
    """차례(쪽번호 달린 제목 줄)를 걷어 낸 본문 줄."""
    out = []
    for ln in text.splitlines():
        s = ln.strip().replace("　", " ")
        if not s:
            continue
        if (ROMAN.match(s) or ITEM.match(s) or NUM.match(s) or ANGLE.match(s)) and TOC_TAIL.search(s) \
                and not s.endswith(("음", "임", "함", "됨", ".")):
            # 차례 줄로 본다(본문 제목에는 쪽번호가 붙지 않는다)
            if re.search(r"[가-힣)\]】]\s*\d{1,4}$", s):
                continue
        out.append(s)
    return out


def sentences(lines):
    """서술 문단만 문장으로 쪼갠다. 제목·캡션·표 자리표시자는 뺀다."""
    sents = []
    for s in lines:
        if s.startswith(("<표>", "<그림>")) or CAPTION.match(s):
            continue
        if ROMAN.match(s) or BRACKET.match(s) or ANGLE.match(s) or ITEM.match(s):
            continue
        if GANA.match(s) and len(s) < 30:
            continue
        if len(HANGUL.findall(s)) < 12:
            continue
        for part in re.split(r"(?<=[가-힣)])\.\s+", s):
            part = part.strip()
            if len(HANGUL.findall(part)) >= 8:
                sents.append(part)
    return sents


def ending_class(sent):
    t = re.sub(r"[\s.\d)\]’'\"�※*]+$", "", sent)
    ch = last_hangul(t[-3:]) if t else ""
    if not ch:
        return "기타", ""
    tail2 = "".join(HANGUL.findall(t))[-2:]
    if ch in "음임함됨":
        return "개조식(음·임·함·됨)", tail2
    if jong(ch) == 16:  # ㅁ 받침: 나타남·보임 등
        return "개조식(기타 ㅁ종결)", tail2
    if ch == "다":
        return "~다", tail2
    return "기타", tail2


PHRASES = {
    # 어조 사다리
    "것으로 보임": r"것으로\s*보임",
    "필요가 있음": r"필요가\s*있음",
    "필요가 있을 것으로 보임": r"필요가\s*있을\s*것으로\s*보임",
    "주의할 필요": r"주의할\s*필요",
    "지양할 필요": r"지양할\s*필요",
    "유의할 필요": r"유의할\s*필요",
    "부적정": r"부적정",
    "부적절": r"부적절",
    "위반한 것임": r"위반한\s*것임",
    "것으로 판단됨": r"것으로\s*판단됨",
    "사료됨": r"사료됨|사료된다",
    # 조치 요구 어간
    "강화할": r"강화할\s*필요",
    "마련할": r"마련할\s*필요",
    "검토할": r"검토할\s*필요",
    "노력할": r"노력할\s*필요",
    "제고할": r"제고할\s*필요",
    "개선할": r"개선할\s*필요",
    "조정할": r"조정할\s*필요",
    "편성할": r"편성할\s*필요",
    "감액": r"감액",
    "증액": r"증액",
    "감액 조정": r"감액\s*조정",
    "증액 조정": r"증액\s*조정",
    "삭감": r"삭감",
    "과다 편성·계상": r"과다\s*(편성|계상)",
    "집행가능": r"집행\s*가능",
    "실집행": r"실집행",
    "계상": r"계상",
    "신규": r"신규",
    "산출근거·단가": r"산출\s*근거|단가",
    "예비타당성": r"예비타당성|예타",
    "총사업비": r"총사업비",
    "사업계획": r"사업\s*계획",
    "이월": r"이월",
    "불용": r"불용",
    "전액 불용": r"전액\s*불용",
    "연례적": r"연례적",
    # 짜임 표지
    "살펴보면": r"살펴보면",
    "따라서": r"따라서",
    "그러나": r"그러나",
    "다만": r"다만",
    "향후": r"향후",
    "에 기인": r"에\s*기인",
    "입장임": r"입장임",
    "설명함": r"설명함|설명하였",
    "에 따르면": r"에\s*따르면",
    "것으로 나타남": r"것으로\s*나타남",
    "점을 고려할 때": r"점을\s*고려할\s*때",
    "면밀히": r"면밀(히|하게)",
    "철저": r"철저",
    "첫째": r"(^|\s)첫째",
    "시정요구": r"시정요구",
}


def title_tail(t):
    t = t.strip()
    for key, pat in [
        ("감액·삭감 검토/필요", r"(감액|삭감)"),
        ("증액 검토/필요", r"증액"),
        ("(예산·편성) 조정 검토/필요", r"조정"),
        ("부적정·부적절", r"부적(정|절)"),
        ("미흡", r"미흡"),
        ("재검토·검토 필요", r"검토\s*필요|재검토"),
        ("유의·주의", r"(유의|주의)"),
        ("~필요(기타)", r"필요\s*(등)?$"),
    ]:
        if re.search(pat, t):
            return key
    return "기타"


def measure(folder):
    files = sorted(Path(folder).glob("*.txt"))
    agg = {"묶음": Path(folder).name, "파일수": len(files)}
    chars = tables = figures = captions = 0
    items, heads = [], Counter()
    endc, tail2 = Counter(), Counter()
    phr = Counter()
    opening = Counter()
    sent_total = 0
    sent_lens = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        chars += len(re.sub(r"\s", "", text))
        tables += text.count("<표>")
        figures += text.count("<그림>")
        lines = body_lines(text)
        captions += sum(1 for s in lines if CAPTION.match(s))
        prev_gana = None
        for i, s in enumerate(lines):
            m = ITEM.match(s)
            if m and not TOC_TAIL.search(s) and len(m.group(2)) <= 80 \
                    and "문의" not in s and "@" not in s and not s.rstrip().endswith("다."):
                items.append((f.stem, m.group(2).strip(), m.group(3).strip()))
            for rx, tag in ((ROMAN, "Ⅰ."), (BRACKET, "【】"), (ANGLE, "< >"), (GANA, "가.")):
                mm = rx.match(s)
                if mm and len(s) < 40:
                    label = re.sub(r"\s+", "", mm.group(1))[:14]
                    heads[tag + " " + label] += 1
            if re.match(r"^\s*나\.\s*검\s*토\s*의\s*견", s):
                prev_gana = i
            elif prev_gana is not None and i == prev_gana + 1:
                first = sentences([s])
                if first:
                    opening[ending_phrase(first[0])] += 1
                prev_gana = None
        sents = sentences(lines)
        sent_total += len(sents)
        for st in sents:
            c, t2 = ending_class(st)
            endc[c] += 1
            if t2:
                tail2[t2] += 1
            sent_lens.append(len(st))
        body = "\n".join(lines)
        for k, pat in PHRASES.items():
            phr[k] += len(re.findall(pat, body, flags=re.M))
    sent_lens.sort()
    agg.update({
        "글자수(공백제외)": chars,
        "표 자리표시자": tables,
        "그림": figures,
        "캡션 붙은 표·그림": captions,
        "검토항목": len(items),
        "검토항목_신규표시": sum(1 for _, a, _ in items if "신규" in a),
        "문장수": sent_total,
        "문장길이_중앙값": sent_lens[len(sent_lens) // 2] if sent_lens else 0,
        "문장길이_평균": round(sum(sent_lens) / len(sent_lens), 1) if sent_lens else 0,
        "종결형": {k: [v, round(100 * v / sent_total, 1)] for k, v in endc.most_common()},
        "끝두글자_상위": [[k, v, round(100 * v / sent_total, 1)] for k, v in tail2.most_common(15)],
        "표현_건수": dict(phr),
        "표현_만자당": {k: round(v / chars * 10000, 2) for k, v in phr.items()},
        "제목_꼬리유형": Counter(title_tail(t) for _, _, t in items).most_common(),
        "검토의견_첫문장_끝말": opening.most_common(12),
        "제목층_상위": heads.most_common(40),
        "검토항목_예시": [f"{a}: {t}" for _, a, t in items[:: max(1, len(items) // 25)]][:25],
    })
    return agg


def ending_phrase(sent):
    t = re.sub(r"[\s.\d)\]’'\"�]+$", "", sent)
    words = t.split()
    tail = " ".join(words[-4:])
    for pat, lab in [
        (r"필요가\s*있을\s*것으로\s*보임$", "~할 필요가 있을 것으로 보임"),
        (r"필요가\s*있음$", "~할 필요가 있음"),
        (r"것으로\s*보임$", "~것으로 보임"),
        (r"부적(정|절)(함|한 것임)?$", "부적정함"),
        (r"것임$", "~것임"),
        (r"나타남$", "~것으로 나타남"),
    ]:
        if re.search(pat, tail):
            return lab
    return "기타:" + "".join(HANGUL.findall(tail))[-4:]




# ── 파일별 골격 탐침(상임위 예비심사처럼 위원회마다 골격이 다른 묶음용) ──
ITEM_PAREN = re.compile(r"^\s*(\d{1,3})\)\s*(.{2,160}?)\s*[:：]\s*(.{2,})$")
ITEM_PAREN_ANY = re.compile(r"^\s*(\d{1,3})\)\s*(.{2,})$")
SUB = re.compile(r"^\s*\((\d{1,2})\)\s*(.{4,})$")
CODE = re.compile(r"\(\d{4}-\d{3}\)|\(\d{2}-\d{2,3}(-\d{3})?\)")


def probe(path):
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = body_lines(text)
    body = [s for s in lines if not TOC_TAIL.search(s) or s.endswith(("음", "임", "함", "됨", "."))]
    # Ⅱ. 검토의견(또는 사업별 검토)이 본문에서 시작하는 위치
    start = None
    for i, s in enumerate(lines):
        if re.match(r"^\s*Ⅱ\.\s*(검\s*토\s*의\s*견|사업별\s*검토)\s*$", s):
            start = i
    pre = sum(len(re.sub(r"\s", "", s)) for s in lines[:start]) if start else None
    tot = sum(len(re.sub(r"\s", "", s)) for s in lines)
    it_dot = [s for s in body if ITEM.match(s)]
    it_par = [s for s in body if ITEM_PAREN.match(s)]
    it_par_any = [s for s in body if ITEM_PAREN_ANY.match(s) and len(s) < 160]
    subs = [s for s in body if SUB.match(s) and len(s) < 120 and re.search(r"(필요|철저|미흡|부적정|검토|개선|문제|강화)\s*(등)?$", s)]
    titles = it_dot + it_par
    return {
        "파일": Path(path).stem,
        "개요 비중(%)": round(100 * pre / tot, 1) if pre else None,
        "항목 1. 사업: 요지": len(it_dot),
        "항목 1) 사업: 요지": len(it_par),
        "1) 줄(요지 없는 것 포함)": len(it_par_any),
        "(1) 지적 소제목": len(subs),
        "제목에 사업코드": sum(1 for s in titles if CODE.search(s)),
        "첫째": len(re.findall(r"(^|\s)첫째", "\n".join(body), flags=re.M)),
        "□ ○ 글머리": sum(1 for s in body if s.startswith(("□", "○"))),
        "예시": (titles or subs)[:2],
    }


if __name__ == "__main__":
    if sys.argv[1] == "--probe":
        rows = [probe(p) for d in sys.argv[2:] for p in sorted(Path(d).glob("*.txt"))]
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(json.dumps([measure(d) for d in sys.argv[1:]], ensure_ascii=False, indent=1))
