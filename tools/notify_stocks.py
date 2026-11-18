#!/usr/bin/env python3
"""장이 끝난 뒤, 사는 조건·파는 조건이 나온 종목을 휴대폰 알림(ntfy)으로 보낸다.

- stocks-data.json 을 읽어 앱(index.html 의 stockRuleRun)과 같은 방식으로 네 가지 기준을 계산한다.
  앱의 계산을 바꾸면 여기도 같이 바꿔야 한다.
- 장이 끝나 확정된 봉만 본다. 장중에는 신호가 생겼다 사라질 수 있어서 보내지 않는다.
- 이미 보낸 것은 stocks-notified.json 에 적어 두고 다시 보내지 않는다.
- 알림을 받을 곳(ntfy 주제 이름)은 환경변수 NTFY_TOPIC 으로 받는다. 저장소가 공개라서 파일에 적지 않는다.
  NTFY_TOPIC 이 없으면 보낼 내용만 화면에 찍고 끝낸다 (시험용).
- 기준에 따른 계산을 알려줄 뿐이며 투자 권유가 아니다.

    NTFY_TOPIC=... python3 tools/notify_stocks.py
"""
import datetime
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = datetime.timezone(datetime.timedelta(hours=9))
APP_URL = 'https://chayoungdal.github.io/my-life/'
RULES = [('ma', '이동평균 교차'), ('macd', 'MACD 교차'), ('rsi', 'RSI 되돌림'), ('brk', '신고가 돌파')]
MAX_AGE_DAYS = 4      # 이보다 오래된 봉의 신호는 보내지 않는다 (오래 멈췄다 다시 돌 때 옛 소식을 보내지 않도록)
KEEP_DAYS = 30        # 보낸 기록을 남겨 두는 기간


# --- 지표 (앱의 TA 와 같은 계산) ---
def sma(a, n):
    out, s = [], 0.0
    for i, x in enumerate(a):
        s += x
        if i >= n:
            s -= a[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(a, n, start=0):
    out = [None] * len(a)
    if len(a) < start + n:
        return out
    e = sum(a[start:start + n]) / n
    out[start + n - 1] = e
    k = 2 / (n + 1)
    for i in range(start + n, len(a)):
        e = a[i] * k + e * (1 - k)
        out[i] = e
    return out


def wilder(a, n, start=0):
    out = [None] * len(a)
    if len(a) < start + n:
        return out
    e = sum(a[start:start + n]) / n
    out[start + n - 1] = e
    for i in range(start + n, len(a)):
        e = (e * (n - 1) + a[i]) / n
        out[i] = e
    return out


def rsi(c, n=14):
    up, dn = [0.0], [0.0]
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        up.append(d if d > 0 else 0.0)
        dn.append(-d if d < 0 else 0.0)
    au, ad = wilder(up, n, 1), wilder(dn, n, 1)
    return [None if au[i] is None else (100.0 if ad[i] == 0 else 100 - 100 / (1 + au[i] / ad[i])) for i in range(len(c))]


def macd(c):
    e12, e26 = ema(c, 12), ema(c, 26)
    line = [None if e12[i] is None or e26[i] is None else e12[i] - e26[i] for i in range(len(c))]
    first = next((i for i, x in enumerate(line) if x is not None), -1)
    sig = [None] * len(c) if first < 0 else ema([0.0 if x is None else x for x in line], 9, first)
    return line, sig


def rule_marks(it, rule):
    """한 기준에서 실제로 사고판 날의 목록 [(자리, 'buy'|'sell')]. 앱의 stockRuleRun 과 같다."""
    h, l, c = it['h'], it['l'], it['c']
    n = len(c)
    st = min(60, n - 1)
    if rule == 'ma':
        x, y = sma(c, 5), sma(c, 20)
    elif rule == 'macd':
        x, y = macd(c)
    elif rule == 'rsi':
        r = rsi(c)

    def cross(k):
        if x[k - 1] is None or y[k - 1] is None or x[k] is None or y[k] is None:
            return None
        if x[k - 1] <= y[k - 1] and x[k] > y[k]:
            return 'buy'
        if x[k - 1] >= y[k - 1] and x[k] < y[k]:
            return 'sell'
        return None

    marks, pos = [], False
    for k in range(max(st, 1), n):
        s = None
        if rule in ('ma', 'macd'):
            s = cross(k)
        elif rule == 'rsi':
            a0, a1 = r[k - 1], r[k]
            if a0 is not None and a1 is not None:
                s = 'buy' if (a0 < 30 and a1 >= 30) else 'sell' if (a0 > 70 and a1 <= 70) else None
        elif rule == 'brk' and k >= 20:
            hi = max(h[max(0, k - 20):k])
            lo = min(l[max(0, k - 10):k])
            s = 'buy' if c[k] > hi else 'sell' if c[k] < lo else None
        if s == 'buy' and not pos:
            pos = True
            marks.append((k, s))
        elif s == 'sell' and pos:
            pos = False
            marks.append((k, s))
    return marks


# --- 확정된 봉 찾기 ---
def last_final_index(it, now):
    """장이 끝나 값이 확정된 가장 최근 봉의 자리. 없으면 None."""
    n = len(it['d'])
    for k in (n - 1, n - 2):
        if k < 1:
            break
        day = datetime.datetime.strptime(it['d'][k], '%Y%m%d').replace(tzinfo=KST)
        if it.get('market') == 'KR':
            done = day + datetime.timedelta(hours=15, minutes=40)          # 한국 장 마감 15:30
        else:
            done = day + datetime.timedelta(days=1, hours=6, minutes=15)   # 미국 장 마감은 한국 시간 다음 날 05:00~06:00
        if now >= done:
            return k
    return None


def collect(data, now):
    """보낼 만한 일들을 모은다: [{key, market, date, name, kind, rule, pct}]"""
    out = []
    for it in data.get('items', []):
        try:
            k = last_final_index(it, now)
            if k is None or k < 61:
                continue
            date = it['d'][k]
            age = (now.date() - datetime.datetime.strptime(date, '%Y%m%d').date()).days
            if age > MAX_AGE_DAYS:
                continue
            market = 'KR' if it.get('market') == 'KR' else 'US'
            base = {'market': market, 'date': date, 'name': it['name']}
            for rule, rule_name in RULES:
                for mk, s in rule_marks(it, rule):
                    if mk == k:
                        out.append(dict(base, key='%s|%s|%s|%s' % (it['id'], date, rule, s), kind=s, rule=rule_name))
            pct = (it['c'][k] / it['c'][k - 1] - 1) * 100
            big = 2 if it.get('kind') == 'index' else 5
            if abs(pct) >= big:
                out.append(dict(base, key='%s|%s|move' % (it['id'], date), kind='up' if pct > 0 else 'down', pct=pct))
        except Exception as e:  # 한 종목의 문제로 전체가 멈추지 않게
            print('건너뜀:', it.get('name'), e)
    return out


def build_message(market, date, events):
    def names(kind):
        seen = {}
        for e in events:
            if e['kind'] == kind:
                seen.setdefault(e['name'], []).append(e.get('rule') or '%+.1f%%' % e['pct'])
        return ', '.join('%s (%s)' % (n, ', '.join(v)) if kind in ('buy', 'sell') else '%s %s' % (n, v[0]) for n, v in seen.items())

    lines = []
    for kind, label in (('buy', '사는 조건'), ('sell', '파는 조건'), ('up', '크게 오름'), ('down', '크게 내림')):
        txt = names(kind)
        if txt:
            lines.append('%s: %s' % (label, txt))
    lines.append('기준에 따른 계산이며 투자 권유가 아닙니다.')
    title = '%s 장 마감 · %d/%d' % ('국내' if market == 'KR' else '미국', int(date[4:6]), int(date[6:8]))
    return title, '\n'.join(lines)


def send(topic, title, message):
    body = json.dumps({'topic': topic, 'title': title, 'message': message, 'click': APP_URL, 'tags': ['chart_with_upwards_trend']}).encode('utf-8')
    req = urllib.request.Request('https://ntfy.sh/', data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as res:
        res.read()


def main():
    now = datetime.datetime.now(KST)
    try:
        with open(os.path.join(ROOT, 'stocks-data.json'), encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print('stocks-data.json 을 읽을 수 없습니다:', e)
        return 0
    state_path = os.path.join(ROOT, 'stocks-notified.json')
    try:
        with open(state_path, encoding='utf-8') as f:
            sent = set(json.load(f).get('sent', []))
    except Exception:
        sent = set()

    events = [e for e in collect(data, now) if e['key'] not in sent]
    if not events:
        print('새로 알릴 것이 없습니다.')
        return 0
    topic = os.environ.get('NTFY_TOPIC', '').strip()
    if topic and not re.match(r'^[A-Za-z0-9_-]{8,64}$', topic):
        print('NTFY_TOPIC 형식이 올바르지 않아 보내지 않습니다.')
        topic = ''

    groups = {}
    for e in events:
        groups.setdefault((e['market'], e['date']), []).append(e)
    done = []
    for (market, date), evs in sorted(groups.items(), key=lambda x: (x[0][1], x[0][0])):
        title, message = build_message(market, date, evs)
        print('---', title)
        print(message)
        if not topic:
            continue
        try:
            send(topic, title, message)
            done.extend(e['key'] for e in evs)
            print('(보냄)')
        except Exception as e:
            print('(보내지 못함: %s)' % str(e)[:80])
    if not topic:
        print('NTFY_TOPIC 이 없어 실제로 보내지는 않았습니다.')
        return 0
    if done:
        limit = (now - datetime.timedelta(days=KEEP_DAYS)).strftime('%Y%m%d')
        keep = sorted(k for k in (sent | set(done)) if k.split('|')[1] >= limit)
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump({'version': 1, 'sent': keep}, f, ensure_ascii=False, indent=0)
            f.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
