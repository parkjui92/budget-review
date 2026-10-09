#!/usr/bin/env python3
"""
fiscal_check — 예산·결산 표의 산술과 표기를 기계로 검산한다.

표 하나가 보고서에 실리기 전에 되묻는 것:
  · 단위를 적었는가
  · 세로 합계가 맞는가 (계·합계·소계 행 대조)
  · 가로 관계식이 맞는가 (예산현액·불용액·집행률·증감)
  · 증감률의 분모를 밝혔는가
  · △(마이너스) 표기가 일관되는가
  · 한 열 안에 단위가 다른 값이 섞이지 않았는가

표준 라이브러리만 쓴다. 입력은 마크다운 표 또는 HTML 표.
통과하지 못하면 종료코드 1을 돌려주므로 파이프라인에서 게이트로 쓸 수 있다.

0.2.0 (budget-review 플러그인 이식본) — 규칙은 그대로, 맞는 표를 틀렸다고 하던 판독 오류를 고쳤다.
  · 합계행 판정: '일반회계'·'○○특별회계'·'국가통계' 처럼 '계'가 든 이름을 합계행으로 읽던 것
  · 소계행: 소계를 전체 항목합과 대조하던 것 → 합계는 항목합·소계합 어느 쪽과 맞아도 통과
  · 이월 열: '전년도이월액'을 불용액 계산에서 빼고, '다음연도이월액'을 예산현액에 더하던 것
  · 예산현액: 구성 열(전년도이월·예비비·이·전용)이 없는 표에서 '예산액 = 예산현액'을 요구하던 것
  · 증감액: 연도 열이 셋 이상일 때 가장 이른 해와 대조하던 것 → (B-A) 표기 또는 최근 두 해
  · 집행률(지출액÷예산현액)·증감률을 표 안의 값으로 다시 계산해 대조(새로 추가)
  · 음수 표기: 홀로 쓴 '-'(해당 없음)를 음수 부호로 읽어 '표기 혼용' 경고를 내던 것
  · 윈도 콘솔(cp949)에서 기호 출력으로 멈추지 않게 표준출력을 UTF-8로

0.2.1 — 기금 표('계획액·계획변경·계획현액') 머리를 읽지 못해 틀린 표를 '통과'로 내보내던 것.
  예산현액 자리의 '계획현액', 예산액 자리의 '계획액', 구성 열 '계획변경'을 같은 관계식으로 읽는다
"""
from __future__ import annotations
import argparse, html as htmlmod, re, sys, unicodedata
from dataclasses import dataclass, field

__version__ = "0.2.1"

# ── 수치 해석 ────────────────────────────────────────────────
MINUS_MARKS = "△▲▽▼−–—"          # 정부 문서에서 음수로 쓰이는 기호들
NUM_RE = re.compile(r"^[\s(]*[" + MINUS_MARKS + r"\-]?[\d,]+(?:\.\d+)?[\s)%]*$")

def to_num(s: str):
    """셀 문자열을 수로. 수가 아니면 None. △·▲·괄호를 음수로 읽는다."""
    if s is None:
        return None
    t = unicodedata.normalize("NFKC", str(s)).strip()
    t = t.replace(" ", " ").strip()
    if t in ("", "-", "‐", "–", "—", "·", ".", "n/a", "N/A"):
        return None
    neg = False
    if t and t[0] in MINUS_MARKS:
        neg, t = True, t[1:]
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    t = t.replace(",", "").replace("%", "").replace("원", "").strip()
    if t.startswith("-"):
        neg, t = True, t[1:]
    if not re.fullmatch(r"\d+(?:\.\d+)?", t):
        return None
    v = float(t)
    return -v if neg else v

def is_numeric_cell(s: str) -> bool:
    return to_num(s) is not None

def neg_style(s: str):
    """음수 표기 방식: 'mark'(△류) / 'dash'(-) / 'paren' / None"""
    t = unicodedata.normalize("NFKC", str(s)).strip()
    if not t or not any(c.isdigit() for c in t):
        return None              # 홀로 쓴 '-'는 '해당 없음' 표시다 — 음수가 아니다
    if t[0] in MINUS_MARKS and t[0] not in "−–—":
        return "mark"
    if t[0] in "−–—" or t.startswith("-"):
        return "dash"
    if t.startswith("(") and t.endswith(")") and any(c.isdigit() for c in t):
        return "paren"
    return None

# ── 표 모델 ──────────────────────────────────────────────────
@dataclass
class Table:
    caption: str
    header: list[str]
    rows: list[list[str]]
    line: int = 0

@dataclass
class Finding:
    level: str      # 'fail' | 'warn'
    table: str
    rule: str
    detail: str

@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    tables: int = 0
    def add(self, *a): self.findings.append(Finding(*a))
    @property
    def fails(self): return [f for f in self.findings if f.level == "fail"]
    @property
    def warns(self): return [f for f in self.findings if f.level == "warn"]

# ── 파서 ─────────────────────────────────────────────────────
def parse_markdown(text: str) -> list[Table]:
    tables, lines = [], text.splitlines()
    i = 0
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and re.fullmatch(
                r"\s*\|?[\s:|-]+\|[\s:|-]*", lines[i + 1] or ""):
            def cells(l):
                l = l.strip()
                if l.startswith("|"): l = l[1:]
                if l.endswith("|"): l = l[:-1]
                return [c.strip() for c in l.split("|")]
            header = cells(lines[i])
            rows, j = [], i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append(cells(lines[j])); j += 1
            cap = ""
            for k in range(max(0, i - 3), i):
                if lines[k].strip() and "|" not in lines[k]:
                    cap = lines[k].strip()
            tables.append(Table(cap, header, rows, i + 1))
            i = j
        else:
            i += 1
    return tables

def parse_html(text: str) -> list[Table]:
    tables = []
    for m in re.finditer(r"<table\b.*?</table>", text, re.S | re.I):
        blk = m.group(0)
        line = text[:m.start()].count("\n") + 1
        cap_m = re.search(r"<caption\b[^>]*>(.*?)</caption>", blk, re.S | re.I)
        cap = strip_tags(cap_m.group(1)) if cap_m else ""
        if not cap:
            pre = text[max(0, m.start() - 400):m.start()]
            hs = re.findall(r"<(?:h[1-6]|p|div)[^>]*>(.*?)</(?:h[1-6]|p|div)>", pre, re.S | re.I)
            cap = strip_tags(hs[-1]) if hs else ""
        trs = re.findall(r"<tr\b.*?</tr>", blk, re.S | re.I)
        header, rows = [], []
        for tr in trs:
            cs = [strip_tags(c) for c in
                  re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", tr, re.S | re.I)]
            if not cs: continue
            if not header and re.search(r"<th\b", tr, re.I):
                header = cs
            else:
                rows.append(cs)
        if header or rows:
            if not header:
                header, rows = rows[0], rows[1:]
            tables.append(Table(cap, header, rows, line))
    return tables

def strip_tags(s: str) -> str:
    return htmlmod.unescape(re.sub(r"<[^>]+>", " ", s)).replace(" ", " ").strip()

# ── 검사 규칙 ────────────────────────────────────────────────
UNIT_RE = re.compile(r"단위\s*[:：]?\s*(원|천원|백만원|억원|조원|천\s*원|백만\s*원|명|건|%)")
RATE_WORDS = ("률", "율", "비중", "비율")
DASHES = {"", "-", "‐", "–", "—", "·", "."}
CHILD_RE = re.compile(r"^\s*([-‐ㆍ·•◦ㅇ└▸]|\(\s*내역)")

def norm(s: str) -> str:
    return re.sub(r"[\s()（）·.]", "", unicodedata.normalize("NFKC", str(s)))

def find_col(header, *words, exclude=()):
    for i, hcell in enumerate(header):
        n = norm(hcell)
        if any(norm(w) in n for w in words) and not any(norm(e) in n for e in exclude):
            return i
    return None

def cols_with(header, word, exclude=RATE_WORDS):
    """word가 든 열을 모두 찾는다(비율 열 제외)."""
    w = norm(word)
    return [i for i, h in enumerate(header)
            if w in norm(h) and not any(norm(e) in norm(h) for e in exclude)]

def is_rate_col(h: str) -> bool:
    return any(k in norm(h) for k in RATE_WORDS)

def row_kind(cell: str) -> str:
    """행 이름으로 합계(grand)·소계(sub)·항목(item)을 가른다.
    '일반회계'·'교통시설특별회계'·'국가통계'·'계속사업'은 항목이다 — '계'가 들었다고 합계가 아니다."""
    lab = norm(cell)
    if not lab:
        return "item"
    if lab in ("계", "전체", "총액") or re.match(r"^계[^가-힣]", lab):
        return "grand"
    if lab.startswith(("총합계", "합계", "총계")):
        return "grand"
    if lab.startswith(("소계", "중계")) or lab.endswith(("소계", "중계", "합계", "총계")):
        return "sub"
    return "item"

def num0(s):
    """구성 열용: 빈칸·'-'는 0으로 읽는다."""
    v = to_num(s)
    if v is None and unicodedata.normalize("NFKC", str(s)).strip() in DASHES:
        return 0.0
    return v

def decimals(s: str) -> int:
    m = re.search(r"\.(\d+)", unicodedata.normalize("NFKC", str(s)))
    return len(m.group(1)) if m else 0

def carry_cols(header):
    """이월 열을 전년도(들어온 것)·다음연도(넘긴 것)·모호로 가른다."""
    prev_c, next_c, amb = [], [], []
    for i, h in enumerate(header):
        n = norm(h)
        if "이월" not in n or is_rate_col(h):
            continue
        if any(k in n for k in ("전년도", "전년", "전기")):
            prev_c.append(i)
        elif any(k in n for k in ("다음연도", "다음년도", "차년도", "익년도", "익년", "차기")):
            next_c.append(i)
        else:
            amb.append(i)
    return prev_c, next_c, amb

def spend_col(header):
    for w in ("지출액", "집행액"):
        c = cols_with(header, w)
        if len(c) == 1:
            return c[0]
        if len(c) > 1:
            return None          # 연도별로 여러 개 — 관계식 대상 아님
    return None

def check_table(t: Table, rep: Report, strict_unit=True):
    name = t.caption or f"(캡션 없는 표 · {t.line}행 부근)"
    blob = " ".join(t.header) + " " + t.caption
    H = t.header

    # 1) 단위 표기
    if not UNIT_RE.search(blob):
        money_like = any(find_col(H, w) is not None
                         for w in ("예산", "결산", "집행", "지출", "금액", "불용", "이월", "증감액"))
        if money_like:
            rep.add("fail" if strict_unit else "warn", name, "단위 미표기",
                    "금액 열이 있는데 표 머리나 캡션에 '(단위: …)'가 없다")

    # 2) 증감률의 분모
    rate_i = find_col(H, "증감률", "증가율", "감소율", "신장률")
    if rate_i is not None:
        h = norm(H[rate_i]) + norm(t.caption)
        explicit = ("본예산", "최종예산", "예산현액", "결산액", "집행액", "전년동기")
        if not any(k in h for k in explicit):
            if "대비" in h or "전년" in h:
                rep.add("warn", name, "증감률 분모 모호",
                        f"'{H[rate_i].strip()}' — '전년 대비'까지만 적혀 있다. "
                        "전년의 무엇인지(본예산/최종예산/예산현액/결산액)를 밝혀야 부처 답변에 버틴다")
            else:
                rep.add("fail", name, "증감률 분모 미표기",
                        f"'{H[rate_i].strip()}' — 무엇 대비인지(전년 본예산/최종예산/예산현액) 밝히지 않았다")

    # 3) 집행률 정의
    rate_x = None
    for i, hc in enumerate(H):
        n = norm(hc)
        if "집행률" in n and "실집행" not in n:
            rate_x = i
            break
    if rate_x is not None:
        den = find_col(H, "예산현액", "계획현액")
        if den is None and find_col(H, "예산액", "본예산") is not None:
            rep.add("warn", name, "집행률 분모 의심",
                    "표에 예산현액 열이 없다 — 집행률의 분모가 예산액이면 과대 계산된다")

    num_cols = [j for j in range(len(H))
                if sum(1 for r in t.rows if j < len(r) and is_numeric_cell(r[j])) >= 2]
    kinds = [row_kind(r[0]) if r else "item" for r in t.rows]
    items = [r for r, k in zip(t.rows, kinds) if k == "item"]
    subs = [r for r, k in zip(t.rows, kinds) if k == "sub"]
    grands = [r for r, k in zip(t.rows, kinds) if k == "grand"]
    tops = [r for r in items if not CHILD_RE.match(r[0] if r else "")]

    def colsum(rows, j):
        vals = [to_num(r[j]) for r in rows if j < len(r)]
        vals = [v for v in vals if v is not None]
        return (sum(vals), len(vals))

    # 4) 세로 합계 — 합계행은 항목합(또는 최상위 항목합·소계합) 가운데 하나와 맞아야 한다
    for tr in grands:
        for j in num_cols:
            if j >= len(tr) or is_rate_col(H[j]):
                continue
            tv = to_num(tr[j])
            if tv is None:
                continue
            tol = max(abs(tv) * 0.005, 1e-9)
            cands = [colsum(g, j) for g in (items, tops, subs)]
            cands = [s for s, n in cands if n >= 2]
            if not cands:
                continue
            if not any(abs(s - tv) <= tol for s in cands):
                s = cands[0]
                rep.add("fail", name, "세로 합계 불일치",
                        f"'{H[j].strip()}' 열 — 합계행 {fmt(tv)} ≠ 항목합 {fmt(s)} (차이 {fmt(s - tv)})")

    # 4-2) 소계 — 바로 위 또는 바로 아래 항목 묶음과 맞는지(묶음 경계가 모호해 경고만)
    for idx, (r, k) in enumerate(zip(t.rows, kinds)):
        if k != "sub":
            continue
        up, m = [], idx - 1
        while m >= 0 and kinds[m] == "item":
            up.append(t.rows[m]); m -= 1
        down, m = [], idx + 1
        while m < len(t.rows) and kinds[m] == "item":
            down.append(t.rows[m]); m += 1
        for j in num_cols:
            if j >= len(r) or is_rate_col(H[j]):
                continue
            sv = to_num(r[j])
            if sv is None:
                continue
            tol = max(abs(sv) * 0.005, 1e-9)
            groups = [colsum(g, j) for g in (up, down)]
            groups = [s for s, n in groups if n >= 1]
            if groups and not any(abs(s - sv) <= tol for s in groups):
                rep.add("warn", name, "소계 확인 필요",
                        f"'{r[0][:26]}' 행 '{H[j].strip()}' — 소계 {fmt(sv)}가 위·아래 항목 묶음 어느 쪽과도 맞지 않는다")
                break

    # 5) 가로 관계식
    def row_label(r):
        return (r[0] if r else "")[:26]

    hx = cols_with(H, "예산현액") or cols_with(H, "계획현액")   # 기금은 '계획현액'(= 예산현액 자리)
    prev_c, next_c, amb = carry_cols(H)
    sp = spend_col(H)

    # 5-1) 예산현액 = 예산액 + 전년도이월 + 예비비 ± 이·전용 — 구성 열이 표에 있을 때만
    if len(hx) == 1:
        ri = hx[0]
        base = cols_with(H, "예산액")
        if not base:
            base = cols_with(H, "본예산") + cols_with(H, "추경")
        if not base:
            base = cols_with(H, "계획액")                       # 기금: 계획액(당초)
        comps = list(dict.fromkeys(prev_c + cols_with(H, "예비비") + cols_with(H, "전용") + cols_with(H, "이용")
                                   + cols_with(H, "계획변경")))
        base = [c for c in base if c != ri and c not in comps]
        if len(base) in (1, 2) and comps and len(prev_c) <= 1:
            bad = 0
            for r in t.rows:
                rv = to_num(r[ri]) if ri < len(r) else None
                if rv is None:
                    continue
                vs = [to_num(r[c]) if c < len(r) else None for c in base] + \
                     [num0(r[c]) if c < len(r) else None for c in comps]
                if any(v is None for v in vs):
                    continue
                calc = sum(vs)
                if abs(calc - rv) > max(abs(rv) * 0.005, 1e-9):
                    bad += 1
                    if bad <= 3:
                        rep.add("fail", name, "가로 검산 불일치 — 예산현액",
                                f"'{row_label(r)}' 행 — 적힌 값 {fmt(rv)} ≠ 계산값 {fmt(calc)}")
            if bad > 3:
                rep.add("fail", name, "가로 검산 불일치 — 예산현액", f"그 밖에 {bad - 3}개 행에서 같은 불일치")

    # 5-2) 불용액 = 예산현액 − 지출액 − 다음연도이월액
    bx = cols_with(H, "불용액") or cols_with(H, "불용")
    if len(hx) == 1 and len(bx) == 1 and sp is not None:
        ri, bi = hx[0], bx[0]
        if len(next_c) == 1:
            carry, sure = next_c[0], True
        elif not next_c and len(amb) == 1:
            carry, sure = amb[0], True       # '이월액' 한 열뿐이면 넘긴 이월로 읽는다
        elif not next_c and not amb:
            carry, sure = None, False        # 이월 열이 없다 — 경고로만
        else:
            carry, sure = "skip", False
        if carry != "skip":
            bad = 0
            for r in t.rows:
                bv = to_num(r[bi]) if bi < len(r) else None
                rv = to_num(r[ri]) if ri < len(r) else None
                sv = to_num(r[sp]) if sp < len(r) else None
                cv = num0(r[carry]) if carry is not None and carry < len(r) else 0.0
                if None in (bv, rv, sv, cv):
                    continue
                calc = rv - sv - cv
                if abs(calc - bv) > max(abs(bv) * 0.005, 1e-9):
                    bad += 1
                    if bad <= 3:
                        if sure:
                            rep.add("fail", name, "가로 검산 불일치 — 불용액",
                                    f"'{row_label(r)}' 행 — 적힌 값 {fmt(bv)} ≠ 계산값 {fmt(calc)}")
                        else:
                            rep.add("warn", name, "불용액 확인 필요",
                                    f"'{row_label(r)}' 행 — 이월 열이 없어 예산현액−지출액({fmt(calc)})으로 대조했다. 적힌 값 {fmt(bv)}")
            if bad > 3 and sure:
                rep.add("fail", name, "가로 검산 불일치 — 불용액", f"그 밖에 {bad - 3}개 행에서 같은 불일치")

    # 5-3) 집행률 = 지출액 ÷ 예산현액 × 100 — 분모를 따로 밝힌 열은 건너뛴다
    if rate_x is not None and len(hx) == 1 and sp is not None:
        hn = norm(H[rate_x])
        if not any(k in hn for k in ("예산액대비", "본예산대비", "최종예산대비", "계획대비")):
            ri, bad = hx[0], 0
            for r in t.rows:
                xv = to_num(r[rate_x]) if rate_x < len(r) else None
                rv = to_num(r[ri]) if ri < len(r) else None
                sv = to_num(r[sp]) if sp < len(r) else None
                if None in (xv, rv, sv) or rv == 0:
                    continue
                calc = sv / rv * 100
                tol = 0.5 * 10 ** (-decimals(r[rate_x])) + 0.01
                if abs(calc - xv) > tol:
                    bad += 1
                    if bad <= 3:
                        rep.add("fail", name, "집행률 불일치",
                                f"'{row_label(r)}' 행 — 적힌 {r[rate_x].strip()} ≠ 지출액÷예산현액 = {calc:.1f}")
            if bad > 3:
                rep.add("fail", name, "집행률 불일치", f"그 밖에 {bad - 3}개 행에서 같은 불일치")

    # 6) 증감액 = 당년 − 전년 — (B-A) 표기가 있으면 그대로, 없으면 최근 두 해
    di = None
    for i, hc in enumerate(H):
        if "증감" in norm(hc) and not is_rate_col(hc):
            di = i
            break
    pair = None
    if di is not None:
        m = re.search(r"([A-Z])\s*[-−–]\s*([A-Z])", unicodedata.normalize("NFKC", H[di]))
        if m:
            def letter_col(x):
                c = [i for i, hc in enumerate(H)
                     if i != di and re.search(r"\(\s*" + x + r"\s*\)", unicodedata.normalize("NFKC", hc))]
                return c[0] if len(c) == 1 else None
            a, b = letter_col(m.group(1)), letter_col(m.group(2))
            if a is not None and b is not None:
                pair = (b, a)          # (전년, 당년)
        if pair is None:
            yrs = {}
            for j, hc in enumerate(H):
                if j == di or is_rate_col(hc):
                    continue
                y = re.search(r"20\d{2}", hc)
                if y:
                    yrs.setdefault(y.group(0), []).append(j)
            ys = sorted(yrs)
            if len(ys) >= 2 and len(yrs[ys[-1]]) == 1 and len(yrs[ys[-2]]) == 1:
                pair = (yrs[ys[-2]][0], yrs[ys[-1]][0])
    if pair is not None:
        prev_i, cur_i = pair
        bad = 0
        for r in t.rows:
            a = to_num(r[cur_i]) if cur_i < len(r) else None
            b = to_num(r[prev_i]) if prev_i < len(r) else None
            d = to_num(r[di]) if di < len(r) else None
            if None in (a, b, d):
                continue
            if abs((a - b) - d) > max(abs(d) * 0.005, 1e-9):
                bad += 1
                if bad <= 3:
                    rep.add("fail", name, "증감액 불일치",
                            f"'{row_label(r)}' 행 — 적힌 증감 {fmt(d)} ≠ {fmt(a)} − {fmt(b)} = {fmt(a - b)}")
        if bad > 3:
            rep.add("fail", name, "증감액 불일치", f"그 밖에 {bad - 3}개 행에서 같은 불일치")

        # 6-2) 증감률 재계산 — 표 안의 두 열로. 분모가 표 밖에 있을 수 있어 경고만
        if rate_i is not None:
            for r in t.rows:
                a = to_num(r[cur_i]) if cur_i < len(r) else None
                b = to_num(r[prev_i]) if prev_i < len(r) else None
                x = to_num(r[rate_i]) if rate_i < len(r) else None
                if None in (a, b, x) or b == 0:
                    continue
                calc = (a - b) / abs(b) * 100
                if abs(calc - x) > 0.5 * 10 ** (-decimals(r[rate_i])) + 0.01:
                    rep.add("warn", name, "증감률 확인 필요",
                            f"'{row_label(r)}' 행 — 적힌 {r[rate_i].strip()} ≠ 표의 두 열로 계산한 {calc:.1f}. "
                            "분모가 표에 없는 값이면 열 머리에 밝힌다")
                    break

    # 7) 음수 표기 혼용
    styles = {}
    for r in t.rows:
        for c in r:
            st = neg_style(c)
            if st: styles.setdefault(st, c.strip())
    if len(styles) > 1:
        rep.add("warn", name, "음수 표기 혼용",
                "한 표에 " + " · ".join(f"{k}({v})" for k, v in styles.items()) +
                " 가 섞여 있다 — 옮겨 적는 과정에서 부호가 사라진다")

    # 8) 한 열의 자릿수 이상 (단위 혼입 의심)
    for j in num_cols:
        if any(k in norm(H[j]) for k in ("률", "율", "비중", "연도", "년도")):
            continue
        mags = []
        for r, k in zip(t.rows, kinds):
            if j >= len(r) or k != "item": continue
            v = to_num(r[j])
            if v and abs(v) >= 1: mags.append(len(str(int(abs(v)))))
        if len(mags) >= 4 and (max(mags) - min(mags)) >= 4:
            rep.add("warn", name, "자릿수 이상",
                    f"'{H[j].strip()}' 열의 자릿수가 {min(mags)}~{max(mags)}자리로 벌어진다 — "
                    "단위가 다른 자료가 섞였는지 확인")

def fmt(v: float) -> str:
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}"
    return f"{v:,.3f}".rstrip("0").rstrip(".")

# ── 실행 ─────────────────────────────────────────────────────
def run(text: str, kind: str = "auto", strict_unit=True) -> Report:
    if kind == "auto":
        kind = "html" if re.search(r"<table\b", text, re.I) else "md"
    tables = parse_html(text) if kind == "html" else parse_markdown(text)
    rep = Report(tables=len(tables))
    for t in tables:
        check_table(t, rep, strict_unit=strict_unit)
    return rep

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="fiscal_check",
        description="예산·결산 표의 산술과 표기를 검산한다 (표준 라이브러리만 사용)")
    ap.add_argument("file", nargs="?", help="검사할 파일 (.md/.html). 생략하면 표준입력")
    ap.add_argument("--format", choices=["auto", "md", "html"], default="auto")
    ap.add_argument("--no-strict-unit", action="store_true",
                    help="단위 미표기를 경고로만 처리")
    ap.add_argument("--quiet", action="store_true", help="통과한 항목은 출력하지 않는다")
    ap.add_argument("--selftest", action="store_true", help="자체검사 실행")
    ap.add_argument("--version", action="version", version=f"fiscal_check {__version__}")
    a = ap.parse_args(argv)

    # 윈도 콘솔(cp949)에서 ✕·△ 출력으로 멈추지 않게 한다
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if a.selftest:
        return selftest()

    text = open(a.file, encoding="utf-8").read() if a.file else sys.stdin.read()
    rep = run(text, a.format, strict_unit=not a.no_strict_unit)

    print(f"검산 대상 표 {rep.tables}개")
    if not rep.findings:
        print("통과 — 되짚을 곳이 없다.")
        return 0
    cur = None
    for f in rep.findings:
        if f.table != cur:
            cur = f.table
            print(f"\n■ {cur}")
        tag = "✕" if f.level == "fail" else "△"
        print(f"  {tag} [{f.rule}] {f.detail}")
    print(f"\n불일치 {len(rep.fails)}건 · 확인 필요 {len(rep.warns)}건")
    if rep.fails:
        print("게이트 불통과 — 표를 고치기 전까지 산출물을 내보내지 않는다.")
        return 1
    return 0

# ── 자체검사 ─────────────────────────────────────────────────
def selftest():
    cases = []
    def case(name, text, rule=None, should_fail=True):
        rep = run(text)
        hit = [f for f in rep.findings if rule is None or f.rule.startswith(rule)]
        ok = bool(hit) == should_fail
        cases.append((name, ok, [f"{f.rule}: {f.detail}" for f in rep.findings]))

    good = """사업별 집행 현황 (단위: 백만원)

| 사업명 | 예산현액 | 지출액 | 다음연도이월 | 불용액 |
|---|---|---|---|---|
| 가사업 | 1,000 | 800 | 150 | 50 |
| 나사업 | 2,000 | 1,900 | 0 | 100 |
| 합계 | 3,000 | 2,700 | 150 | 150 |
"""
    case("정상 표는 조용하다", good, None, should_fail=False)

    case("세로 합계 불일치", good.replace("| 합계 | 3,000 |", "| 합계 | 3,100 |"),
         "세로 합계")
    case("가로 검산 불일치 — 불용액",
         good.replace("| 가사업 | 1,000 | 800 | 150 | 50 |",
                      "| 가사업 | 1,000 | 800 | 150 | 90 |"), "가로 검산")
    case("단위 미표기", good.replace(" (단위: 백만원)", ""), "단위 미표기")
    case("증감률 분모 미표기", """추이 (단위: 백만원)

| 사업명 | 2024년 | 2025년 | 증감률 |
|---|---|---|---|
| 가사업 | 100 | 120 | 20.0 |
| 나사업 | 200 | 210 | 5.0 |
""", "증감률 분모")
    case("증감률 분모를 밝히면 통과", """추이 (단위: 백만원)

| 사업명 | 2024년 | 2025년 | 증감률(전년 본예산 대비) |
|---|---|---|---|
| 가사업 | 100 | 120 | 20.0 |
""", "증감률 분모", should_fail=False)
    case("전년 대비까지만 적으면 경고", """전년 대비 증감 (단위: 백만원)

| 사업명 | 2024년 | 2025년 | 증감률 |
|---|---|---|---|
| 가사업 | 100 | 120 | 20.0 |
""", "증감률 분모 모호")
    case("증감액 불일치", """추이 (단위: 백만원)

| 사업명 | 2024년 | 2025년 | 증감액 |
|---|---|---|---|
| 가사업 | 100 | 120 | 25 |
| 나사업 | 200 | 210 | 10 |
""", "증감액")
    case("△를 음수로 읽는다", """추이 (단위: 백만원)

| 사업명 | 2024년 | 2025년 | 증감액 |
|---|---|---|---|
| 가사업 | 100 | 80 | △20 |
| 나사업 | 200 | 150 | △50 |
""", "증감액", should_fail=False)
    case("음수 표기 혼용은 경고", """추이 (단위: 백만원)

| 사업명 | 2024년 | 2025년 | 증감액 |
|---|---|---|---|
| 가사업 | 100 | 80 | △20 |
| 나사업 | 200 | 150 | -50 |
""", "음수 표기")
    case("집행률 분모 의심", """현황 (단위: 백만원)

| 사업명 | 예산액 | 지출액 | 집행률 |
|---|---|---|---|
| 가사업 | 1,000 | 800 | 80.0 |
| 나사업 | 2,000 | 1,000 | 50.0 |
""", "집행률 분모")
    case("HTML 표도 읽는다", """<p>집행 현황 (단위: 백만원)</p>
<table><tr><th>사업명</th><th>예산현액</th><th>지출액</th><th>다음연도이월</th><th>불용액</th></tr>
<tr><td>가사업</td><td>1,000</td><td>800</td><td>150</td><td>90</td></tr></table>""",
         "가로 검산")
    case("비율 열은 합산하지 않는다", """현황 (단위: 백만원)

| 사업명 | 예산현액 | 지출액 | 집행률 |
|---|---|---|---|
| 가사업 | 1,000 | 800 | 80.0 |
| 나사업 | 2,000 | 1,000 | 50.0 |
| 합계 | 3,000 | 1,800 | 60.0 |
""", "세로 합계", should_fail=False)
    case("자릿수 이상(단위 혼입)", """현황 (단위: 백만원)

| 사업명 | 예산현액 | 지출액 |
|---|---|---|
| 가사업 | 1,200 | 1,100 |
| 나사업 | 2,400 | 2,300 |
| 다사업 | 3,100 | 3,000 |
| 라사업 | 45,000,000 | 44,000,000 |
""", "자릿수 이상")

    # ── 0.2.1 기금 표 — 계획현액 머리 ──
    fund = """기금 사업 결산 (단위: 백만원)

| 사업명 | 계획액 | 전년도이월액 | 계획변경 | 계획현액(A) | 집행액(B) | 다음연도이월액 | 불용액 | 집행률(B/A) |
|---|---|---|---|---|---|---|---|---|
| 가사업 | 10,000 | 500 | △1,000 | 9,500 | 8,000 | 700 | 800 | 84.2 |
"""
    case("기금 표(계획현액)는 조용하다", fund, None, should_fail=False)
    case("기금 표 — 불용액 불일치를 잡는다",
         fund.replace("| 700 | 800 | 84.2 |", "| 700 | 900 | 84.2 |"), "가로 검산 불일치 — 불용액")
    case("기금 표 — 집행률 불일치를 잡는다",
         fund.replace("| 700 | 800 | 84.2 |", "| 700 | 800 | 90.0 |"), "집행률 불일치")
    case("기금 표 — 계획현액 구성 불일치를 잡는다",
         fund.replace("| 9,500 | 8,000 | 700 | 800 | 84.2 |", "| 10,500 | 8,000 | 700 | 1,800 | 76.2 |"),
         "가로 검산 불일치 — 예산현액")

    # ── 0.2.0 회귀 — 맞는 표를 틀렸다고 하지 않는다 ──
    case("'○○회계'·'○○기금' 행은 합계행이 아니다", """소관 세출 결산 (단위: 백만원)

| 구분 | 예산현액 | 지출액 | 다음연도이월 | 불용액 |
|---|---|---|---|---|
| 일반회계 | 1,000 | 800 | 150 | 50 |
| 교통시설특별회계 | 2,000 | 1,900 | 0 | 100 |
| 국민체육진흥기금 | 500 | 400 | 50 | 50 |
| 전력산업기반기금 | 300 | 300 | 0 | 0 |
| 합계 | 3,800 | 3,400 | 200 | 200 |
""", None, should_fail=False)
    case("사업명에 '통계'·'계속'이 들어도 항목이다", """사업별 결산 (단위: 백만원)

| 사업명 | 예산현액 | 지출액 | 다음연도이월 | 불용액 |
|---|---|---|---|---|
| 국가통계 품질관리 | 1,000 | 800 | 150 | 50 |
| 계속사업 정비 | 2,000 | 1,900 | 0 | 100 |
| 지역상생 | 500 | 400 | 50 | 50 |
| 합계 | 3,500 | 3,100 | 200 | 200 |
""", None, should_fail=False)
    case("'회계' 행이 섞여도 진짜 합계 오류는 잡는다", """소관 세출 결산 (단위: 백만원)

| 구분 | 예산현액 | 지출액 |
|---|---|---|
| 일반회계 | 1,000 | 800 |
| 교통시설특별회계 | 2,000 | 1,900 |
| 합계 | 3,100 | 2,700 |
""", "세로 합계")
    case("소계가 있어도 합계는 항목합과 대조한다", """회계별 결산 (단위: 백만원)

| 구분 | 예산현액 | 지출액 |
|---|---|---|
| 가사업 | 1,000 | 800 |
| 나사업 | 2,000 | 1,900 |
| 일반회계 소계 | 3,000 | 2,700 |
| 다사업 | 500 | 400 |
| 라사업 | 300 | 300 |
| 기금 소계 | 800 | 700 |
| 합계 | 3,800 | 3,400 |
""", None, should_fail=False)
    case("소계가 틀리면 확인 필요", """회계별 결산 (단위: 백만원)

| 구분 | 예산현액 | 지출액 |
|---|---|---|
| 가사업 | 1,000 | 800 |
| 나사업 | 2,000 | 1,900 |
| 일반회계 소계 | 3,100 | 2,700 |
| 다사업 | 500 | 400 |
| 합계 | 3,500 | 3,100 |
""", "소계 확인")
    full = """사업 결산 현황 (단위: 백만원)

| 사업명 | 예산액 | 전년도이월액 | 이·전용 등 | 예산현액 | 지출액 | 다음연도이월액 | 불용액 | 집행률 |
|---|---|---|---|---|---|---|---|---|
| 가사업 | 1,000 | 200 | - | 1,200 | 900 | 200 | 100 | 75.0 |
| 나사업 | 500 | 0 | △50 | 450 | 400 | 0 | 50 | 88.9 |
"""
    case("전년도이월액·다음연도이월액을 제자리에 쓴다", full, None, should_fail=False)
    case("예산현액 구성이 틀리면 잡는다",
         full.replace("| 가사업 | 1,000 | 200 | - | 1,200 |", "| 가사업 | 1,000 | 300 | - | 1,200 |"),
         "가로 검산 불일치 — 예산현액")
    case("불용액이 틀리면 잡는다(이월 열 구분 후)",
         full.replace("| 200 | 100 | 75.0 |", "| 200 | 300 | 75.0 |"), "가로 검산 불일치 — 불용액")
    case("집행률을 다시 계산한다",
         full.replace("| 100 | 75.0 |", "| 100 | 80.0 |"), "집행률 불일치")
    case("구성 열 없는 연도별 추이는 예산현액≠예산액이어도 통과", """연도별 집행 추이 (단위: 백만원)

| 연도 | 예산액 | 예산현액 | 지출액 | 이월액 | 불용액 | 집행률 |
|---|---|---|---|---|---|---|
| 2023 | 1,000 | 1,150 | 900 | 150 | 100 | 78.3 |
| 2024 | 1,100 | 1,250 | 1,000 | 200 | 50 | 80.0 |
| 2025 | 1,200 | 1,400 | 1,100 | 200 | 100 | 78.6 |
""", "가로 검산", should_fail=False)
    case("연도별 예산현액 비교표는 관계식 대상이 아니다", """예산현액 연도 비교 (단위: 백만원)

| 사업명 | 2024년 예산현액 | 2025년 예산현액 | 증감액 |
|---|---|---|---|
| 가사업 | 1,000 | 1,200 | 200 |
| 나사업 | 900 | 800 | △100 |
""", None, should_fail=False)
    case("연도 열이 셋이면 (C-B) 표기를 따른다", """예산 추이 (단위: 백만원)

| 사업명 | 2024년 결산(A) | 2025년 예산(B) | 2026년 예산안(C) | 증감(C-B) | 증감률(2025년 본예산 대비) |
|---|---|---|---|---|---|
| 가사업 | 900 | 1,000 | 1,200 | 200 | 20.0 |
| 나사업 | 700 | 800 | 600 | △200 | △25.0 |
""", None, should_fail=False)
    case("(C-B)대로 계산해 틀리면 잡는다", """예산 추이 (단위: 백만원)

| 사업명 | 2024년 결산(A) | 2025년 예산(B) | 2026년 예산안(C) | 증감(C-B) |
|---|---|---|---|---|
| 가사업 | 900 | 1,000 | 1,200 | 300 |
| 나사업 | 700 | 800 | 600 | △200 |
""", "증감액")

    bad = [c for c in cases if not c[1]]
    for name, ok, det in cases:
        print(("  ok  " if ok else "  FAIL") + f"  {name}")
        if not ok:
            for d in det: print(f"          └ {d}")
    print(f"\n자체검사 {len(cases) - len(bad)}/{len(cases)} 통과")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
