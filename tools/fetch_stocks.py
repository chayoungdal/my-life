#!/usr/bin/env python3
"""관심 종목의 일봉 시세를 받아 stocks-data.json 을 만든다.
같은 자료를 한 주·한 달씩 묶어 주봉·월봉용 stocks-long.json 도 만든다.

- 무엇을 받을지는 stocks.json 에 적는다 (종목·지수 목록).
- 자료는 네이버 금융의 차트 API에서 받는다. 공식 공개 API가 아니어서 형식이 바뀌면 이 스크립트를 고쳐야 한다.
- 어떤 종목을 받지 못하면 그 종목은 이전에 받아 둔 자료를 그대로 둔다.
- 받은 내용이 이전과 같으면 파일을 다시 쓰지 않는다.
- 외부 패키지 없이 파이썬 기본 기능만 쓴다.

    python3 tools/fetch_stocks.py           # 저장소 폴더에서 실행
"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = 'https://api.stock.naver.com/chart/%s/day?startDateTime=%s&endDateTime=%s'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
KEEP_ROWS = 320       # 종목마다 남겨 두는 거래일 수 (120일선과 52주 고저를 계산할 만큼)
KEEP_WEEKS = 160      # 주봉으로 남겨 두는 주 수 (3년쯤)
KEEP_MONTHS = 60      # 월봉으로 남겨 두는 달 수 (5년)
FETCH_DAYS = 1900     # 이만큼의 달력 날짜를 요청한다 (월봉 5년치를 만들 만큼)
MIN_ROWS = 30         # 이보다 적게 오면 잘못 받은 것으로 본다
KST = datetime.timezone(datetime.timedelta(hours=9))
SRC_PREFIXES = ('domestic/item/', 'domestic/index/', 'foreign/item/', 'foreign/index/')


def fetch(src, start, end):
    req = urllib.request.Request(API % (src, start, end), headers={'User-Agent': UA, 'Accept': 'application/json'})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                return json.loads(res.read().decode('utf-8'))
        except Exception as e:  # 잠깐의 오류는 다시 시도
            last = e
            time.sleep(2 + attempt * 3)
    raise last


def to_columns(rows):
    """API의 행 목록을 날짜순 열 묶음으로 바꾼다. 값이 이상한 행은 버린다."""
    out = {'d': [], 'o': [], 'h': [], 'l': [], 'c': [], 'v': []}
    seen = set()
    for r in sorted(rows, key=lambda x: str(x.get('localDate', ''))):
        d = str(r.get('localDate', ''))
        try:
            o, h, l, c = (float(r['openPrice']), float(r['highPrice']), float(r['lowPrice']), float(r['closePrice']))
            v = int(float(r.get('accumulatedTradingVolume') or 0))
        except Exception:
            continue
        if len(d) != 8 or not d.isdigit() or d in seen:
            continue
        if min(o, h, l, c) <= 0 or h < l:
            continue
        seen.add(d)
        out['d'].append(d)
        for k, val in (('o', o), ('h', h), ('l', l), ('c', c)):
            out[k].append(round(val, 4))
        out['v'].append(v)
    return out


def tail(cols, n):
    return {k: v[-n:] for k, v in cols.items()}


def group(cols, unit, keep):
    """일봉을 한 주('w') 또는 한 달('m')씩 묶는다. 날짜는 그 묶음의 마지막 거래일이다. 앱의 stockAgg 와 같은 방식."""
    out = {'d': [], 'o': [], 'h': [], 'l': [], 'c': [], 'v': []}
    last = None
    for i, d in enumerate(cols['d']):
        if unit == 'm':
            key = d[:6]
        else:
            day = datetime.date(int(d[:4]), int(d[4:6]), int(d[6:8]))
            key = (day - datetime.timedelta(days=day.weekday())).isoformat()  # 그 주 월요일
        if key != last:
            last = key
            for k in out:
                out[k].append(cols[k][i])
        else:
            out['d'][-1] = d
            out['h'][-1] = max(out['h'][-1], cols['h'][i])
            out['l'][-1] = min(out['l'][-1], cols['l'][i])
            out['c'][-1] = cols['c'][i]
            out['v'][-1] += cols['v'][i]
    return tail(out, keep)


def main():
    try:
        with open(os.path.join(ROOT, 'stocks.json'), encoding='utf-8') as f:
            watch = json.load(f)['items']
    except Exception as e:
        print('오류: stocks.json 을 읽을 수 없습니다:', e)
        return 1
    out_path = os.path.join(ROOT, 'stocks-data.json')
    long_path = os.path.join(ROOT, 'stocks-long.json')
    old = {}
    try:
        with open(out_path, encoding='utf-8') as f:
            prev = json.load(f)
        old = {it['id']: it for it in prev.get('items', [])}
    except Exception:
        prev = None
    old_long = {}
    try:
        with open(long_path, encoding='utf-8') as f:
            prev_long = json.load(f)
        old_long = {it['id']: it for it in prev_long.get('items', [])}
    except Exception:
        prev_long = None

    today = datetime.datetime.now(KST)
    start = (today - datetime.timedelta(days=FETCH_DAYS)).strftime('%Y%m%d')
    end = today.strftime('%Y%m%d')
    items, long_items, ok, failed = [], [], 0, []
    for w in watch:
        wid, name, src = str(w.get('id', '')), str(w.get('name', '')), str(w.get('src', ''))
        if not wid or not name or not src.startswith(SRC_PREFIXES) or any(ch in src for ch in '?#&% '):
            failed.append('%s (stocks.json 형식 오류)' % (wid or name or '?'))
            continue
        base = {'id': wid, 'name': name, 'kind': w.get('kind', 'stock'), 'market': w.get('market', 'KR'), 'cur': w.get('cur', 'KRW')}
        try:
            cols = to_columns(fetch(src, start, end))
            if len(cols['d']) < MIN_ROWS:
                raise ValueError('자료가 %d일치뿐입니다' % len(cols['d']))
            long_items.append({'id': wid, 'w': group(cols, 'w', KEEP_WEEKS), 'm': group(cols, 'm', KEEP_MONTHS)})
            base.update(tail(cols, KEEP_ROWS))
            items.append(base)
            ok += 1
        except Exception as e:
            failed.append('%s (%s)' % (name, str(e)[:80]))
            if wid in old:  # 이전 자료 유지
                keep = dict(old[wid])
                keep.update({'name': name, 'kind': base['kind'], 'market': base['market'], 'cur': base['cur']})
                items.append(keep)
            if wid in old_long:
                long_items.append(old_long[wid])
        time.sleep(0.4)  # 서버에 부담을 주지 않도록 천천히

    if ok == 0:
        print('오류: 한 종목도 받지 못했습니다. 파일을 바꾸지 않습니다.')
        for f_ in failed:
            print(' -', f_)
        return 1

    same = prev is not None and prev.get('items') == items
    if same:
        print('변경 없음 (%d개 종목)' % len(items))
    else:
        data = {'version': 1, 'updated_at': today.strftime('%Y-%m-%dT%H:%M:%S+09:00'), 'source': '네이버 금융', 'items': items}
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
            f.write('\n')
        print('저장함: %d개 종목 (새로 받음 %d개)' % (len(items), ok))
    if not (prev_long is not None and prev_long.get('items') == long_items):
        data = {'version': 1, 'updated_at': today.strftime('%Y-%m-%dT%H:%M:%S+09:00'), 'source': '네이버 금융', 'items': long_items}
        with open(long_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
            f.write('\n')
        print('주봉·월봉 저장함: %d개 종목' % len(long_items))
    for f_ in failed:
        print('받지 못함:', f_)
    return 0


if __name__ == '__main__':
    sys.exit(main())
